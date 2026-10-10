#!/usr/bin/env python3
"""Revision diaria de canales: lee channels.json y escribe status.json.

Que comprueba:
  - Canales de YouTube "en vivo aqui": si estan transmitiendo ahora y si YouTube
    permite verlos dentro de otra pagina.
  - Canales que abren en pestana: si el sitio responde.

Reglas de seguridad:
  - NUNCA modifica channels.json: solo escribe status.json.
  - Ante cualquier duda el resultado es "unknown" y la pagina lo ignora. Un canal
    solo se marca como problema cuando hay evidencia clara.
  - Solo usa la libreria estandar de Python.
"""
import http.client
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
TIMEOUT = 20
HEARTBEAT = timedelta(days=3)       # aunque nada cambie, se actualiza la fecha cada 3 dias
BAD = ("offline", "down", "noembed")
YT_HEADERS = {"Cookie": "CONSENT=YES+cb.20240101-00-p0.es+FX+000; SOCS=CAI"}
YT_DELAY = 2.0                      # pausa entre pedidos a YouTube (si no, responde 429)
YT_RETRIES = 3                      # intentos cuando YouTube responde 429
YT_BUDGET = 8 * 60                  # segundos maximos dedicados a YouTube en cada revision
YT_MAX_BLOCKED = 3                  # tras 3 canales seguidos bloqueados (429) se deja de insistir

LIVE_RE = re.compile(r"youtube\.com/embed/live_stream\?channel=(UC[\w-]{10,})")
PLAYLIST_RE = re.compile(r"youtube\.com/embed/videoseries\?list=([\w-]+)")
VIDEO_RE = re.compile(r"youtube\.com/embed/([\w-]{6,})")


class NetError(Exception):
    """No hubo respuesta. kind: 'dns' | 'timeout' | 'other'."""
    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


def http_get(url, headers=None):
    """Devuelve (codigo_http, texto). Lanza NetError si no hubo respuesta."""
    h = {"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9,en;q=0.5",
         "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(3_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except urllib.error.URLError as e:
        if isinstance(e.reason, socket.gaierror):
            raise NetError("dns")
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise NetError("timeout")
        raise NetError("other")
    except (socket.timeout, TimeoutError):
        raise NetError("timeout")
    except (ssl.SSLError, ConnectionError, OSError, http.client.HTTPException):
        raise NetError("other")


def analyze_live_page(html):
    """Lee youtube.com/channel/UC.../live. Devuelve:
       ("live", video_id)  -> hay una transmision en vivo ahora (se confirma con oEmbed)
       ("offline", "")     -> la pagina del canal cargo bien y no hay transmision
       ("unknown", razon)  -> no se puede asegurar; la razon incluye las marcas halladas
    """
    if "ytInitialData" not in html and "ytInitialPlayerResponse" not in html:
        return "unknown", "pagina sin datos de YouTube (consentimiento o bloqueo)"
    canon = (re.search(r'<link[^>]+rel="canonical"[^>]+href="https://www\.youtube\.com/watch\?v=([\w-]{11})', html)
             or re.search(r'<link[^>]+href="https://www\.youtube\.com/watch\?v=([\w-]{11})"[^>]+rel="canonical"', html))
    details = re.search(r'"videoDetails"\s*:\s*\{\s*"videoId"\s*:\s*"([\w-]{11})"', html)
    live_now = bool(re.search(r'"isLiveNow"\s*:\s*true', html) or re.search(r'"isLive"\s*:\s*true', html))
    video_id = (canon or details).group(1) if (canon or details) else None
    if video_id and live_now:
        return "live", video_id
    if re.search(r'<link[^>]+rel="canonical"[^>]+href="https://www\.youtube\.com/(channel/|@)', html):
        return "offline", ""
    marks = "isLiveNow=%d isLive=%d isLiveContent=%d canonical=%s videoDetails=%d" % (
        bool(re.search(r'"isLiveNow"\s*:\s*true', html)), bool(re.search(r'"isLive"\s*:\s*true', html)),
        bool(re.search(r'"isLiveContent"\s*:\s*true', html)), "watch" if canon else "no", bool(details))
    return "unknown", "no se pudo ubicar la transmision (%s)" % marks


def oembed_state(video_id, get, sleep):
    """Pregunta a YouTube (oEmbed) si un video existe y permite integrarse en otra pagina."""
    return oembed_state_url("https://www.youtube.com/watch?v=%s" % video_id, get, sleep)


def oembed_state_url(page_url, get, sleep):
    """Lo mismo para cualquier direccion de YouTube (video o lista de reproduccion)."""
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(page_url, safe="")
    try:
        code, _ = yt_get(url, get, sleep)
    except NetError:
        return "unknown", "sin respuesta de YouTube (oEmbed)"
    if code == 200:
        return "ok", ""
    if code == 401:
        return "noembed", ""                      # el dueno desactivo la integracion
    if code == 404:
        return "offline", "video no disponible"   # borrado o privado
    return "unknown", "oEmbed respondio %s" % code


def classify_http(code):
    if 200 <= code < 400:
        return "ok"
    if code in (404, 410):
        return "down"
    return "unknown"           # 403/429/5xx...: muchos sitios (ej. Amazon) rechazan a los robots


def yt_get(url, get, sleep):
    """Pide una pagina de YouTube; si responde 429 espera y reintenta."""
    code, html = get(url, YT_HEADERS)
    for attempt in range(1, YT_RETRIES):
        if code != 429:
            break
        sleep(15 * attempt)
        code, html = get(url, YT_HEADERS)
    return code, html


def check_channel(ch, get=http_get, sleep=time.sleep):
    """Devuelve (estado, detalle) para un canal."""
    url = ch.get("url", "")
    direct = bool(ch.get("direct"))
    m = None if direct else LIVE_RE.search(url)
    if m:
        try:
            code, html = yt_get("https://www.youtube.com/channel/%s/live" % m.group(1), get, sleep)
        except NetError:
            return "unknown", "sin respuesta de YouTube"
        if code != 200:
            return "unknown", "YouTube respondio %s" % code
        kind, value = analyze_live_page(html)
        if kind == "live":
            return oembed_state(value, get, sleep)
        return (kind, "") if kind == "offline" else ("unknown", value)
    pl = None if direct else PLAYLIST_RE.search(url)
    if pl:
        return oembed_state_url("https://www.youtube.com/playlist?list=%s" % pl.group(1), get, sleep)
    v = None if direct else VIDEO_RE.search(url)
    if v and v.group(1) not in ("live_stream", "videoseries"):
        return oembed_state(v.group(1), get, sleep)
    # Canal que abre en pestana: basta con que el sitio responda
    try:
        code, _ = get(url)
    except NetError as e:
        if e.kind == "dns":
            return "down", "no se pudo resolver el sitio"
        return "unknown", "sin respuesta (%s)" % e.kind
    return classify_http(code), "HTTP %s" % code


def merge(channels, results, old, now):
    """Arma el bloque 'channels' de status.json.

    Los dias con problema se cuentan por FECHA (no por cantidad de ejecuciones): si el
    proceso corre dos veces el mismo dia, el numero no cambia. Un dia sin datos
    ('unknown') no rompe la cuenta de un problema que venia de antes.
    """
    today = now.date()
    old_ch = (old or {}).get("channels", {})
    out = {}
    for ch in channels:
        cid = ch["id"]
        state, detail = results[cid]
        url = ch.get("url", "")
        prev = old_ch.get(cid, {})
        if prev.get("url") != url:
            prev = {}           # el canal se edito: se empieza de cero
        prev_bad = prev.get("state") if prev.get("state") in BAD else prev.get("badState")
        since = prev.get("since")
        entry = {"state": state, "url": url}
        if state in BAD:
            if not (prev_bad == state and since):
                since = today.isoformat()
            entry["since"] = since
        elif state == "ok":
            since = None
        else:  # unknown: se conserva lo que se sabia antes
            if prev_bad and since:
                entry["since"] = since
                entry["badState"] = prev_bad
            else:
                since = None
        try:
            fails = (today - datetime.fromisoformat(since).date()).days + 1 if since else 0
        except Exception:
            fails = 0
        entry["fails"] = fails
        if detail:
            entry["detail"] = detail
        out[cid] = entry
    return out


def should_write(new_channels, old, now):
    if not old or "channels" not in old:
        return True
    if old["channels"] != new_channels:
        return True
    try:
        last = datetime.fromisoformat(old["checkedAt"].replace("Z", "+00:00"))
    except Exception:
        return True
    return now - last >= HEARTBEAT


def uses_youtube_page(ch):
    if ch.get("direct"):
        return False
    url = ch.get("url", "")
    v = VIDEO_RE.search(url)
    return bool(LIVE_RE.search(url) or PLAYLIST_RE.search(url) or (v and v.group(1) not in ("live_stream", "videoseries")))


def main(root=".", get=http_get, now=None, sleep=time.sleep, clock=time.monotonic):
    now = now or datetime.now(timezone.utc)
    with open(os.path.join(root, "channels.json"), encoding="utf-8") as f:
        channels = json.load(f)
    status_path = os.path.join(root, "status.json")
    old = None
    if os.path.exists(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            old = None

    def safe_check(ch):
        try:
            return check_channel(ch, get, sleep)
        except Exception as e:  # un canal con problemas nunca debe frenar la revision de los demas
            return "unknown", "error interno: %s" % type(e).__name__

    results = {}
    others = [c for c in channels if not uses_youtube_page(c)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        for ch, o in zip(others, ex.map(safe_check, others)):
            results[ch["id"]] = o
    # YouTube se consulta de a uno y con pausas: desde un servidor, en paralelo responde 429
    started = clock()
    blocked_in_a_row = 0
    for i, ch in enumerate([c for c in channels if uses_youtube_page(c)]):
        if blocked_in_a_row >= YT_MAX_BLOCKED:
            results[ch["id"]] = ("unknown", "YouTube limita las consultas desde este servidor; se reintenta en la proxima revision")
            continue
        if clock() - started > YT_BUDGET:
            results[ch["id"]] = ("unknown", "se agoto el tiempo de la revision; se reintenta en la proxima")
            continue
        if i:
            sleep(YT_DELAY + random.random())
        outcome = safe_check(ch)
        results[ch["id"]] = outcome
        blocked_in_a_row = blocked_in_a_row + 1 if (outcome[0] == "unknown" and "429" in outcome[1]) else 0
    new_channels = merge(channels, results, old, now)

    lines = ["| Canal | Estado | Dias seguidos | Detalle |", "|---|---|---|---|"]
    for ch in channels:
        e = new_channels[ch["id"]]
        lines.append("| %s | %s | %s | %s |" % (ch.get("name", ch["id"]), e["state"], e["fails"], e.get("detail", "")))
    summary = "\n".join(lines)
    print(summary)
    counts = {}
    for e in new_channels.values():
        counts[e["state"]] = counts.get(e["state"], 0) + 1
    print("\nResumen:", counts)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Revision de canales\n\n" + summary + "\n\nResumen: %s\n" % counts)

    if should_write(new_channels, old, now):
        data = {"checkedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "channels": new_channels}
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("status.json actualizado.")
        return True
    print("Sin cambios: status.json no se toca.")
    return False


if __name__ == "__main__":
    main(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
