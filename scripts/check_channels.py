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
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import urllib.error
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

LIVE_RE = re.compile(r"youtube\.com/embed/live_stream\?channel=(UC[\w-]{10,})")
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
    except (ssl.SSLError, ConnectionError, OSError):
        raise NetError("other")


def explain_live_page(html):
    """(estado, razon) segun el HTML de youtube.com/channel/UC.../live.

    ok       -> transmitiendo ahora y YouTube permite integrarlo
    noembed  -> transmitiendo ahora pero YouTube NO permite integrarlo
    offline  -> la pagina cargo bien y no hay ninguna transmision en vivo
    unknown  -> no se puede asegurar (se explica por que en la razon)
    """
    if "ytInitialData" not in html and "ytInitialPlayerResponse" not in html:
        return "unknown", "pagina sin datos de YouTube (consentimiento o bloqueo)"
    if re.search(r'"isLiveNow"\s*:\s*true', html):
        m = re.search(r'"playableInEmbed"\s*:\s*(true|false)', html)
        if not m:
            return "unknown", "en vivo, pero YouTube no informa si se puede integrar"
        return ("ok" if m.group(1) == "true" else "noembed"), ""
    if re.search(r'"isLive(Content)?"\s*:\s*true', html):
        return "unknown", "senales ambiguas de transmision"
    return "offline", ""


def parse_live_page(html):
    return explain_live_page(html)[0]


def parse_video_page(html):
    """Estado segun el HTML de youtube.com/watch?v=ID (video individual)."""
    m = re.search(r'"playableInEmbed"\s*:\s*(true|false)', html)
    if m:
        return "ok" if m.group(1) == "true" else "noembed"
    if re.search(r'"playabilityStatus"\s*:\s*\{\s*"status"\s*:\s*"ERROR"', html):
        return "offline"        # el video ya no existe
    return "unknown"


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
        return explain_live_page(html)
    v = None if direct else VIDEO_RE.search(url)
    if v and v.group(1) != "live_stream":
        try:
            code, html = yt_get("https://www.youtube.com/watch?v=%s" % v.group(1), get, sleep)
        except NetError:
            return "unknown", "sin respuesta de YouTube"
        if code != 200:
            return "unknown", "YouTube respondio %s" % code
        return parse_video_page(html), ""
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
    return bool(LIVE_RE.search(url) or (v and v.group(1) != "live_stream"))


def main(root=".", get=http_get, now=None, sleep=time.sleep):
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
    for i, ch in enumerate([c for c in channels if uses_youtube_page(c)]):
        if i:
            sleep(YT_DELAY + random.random())
        results[ch["id"]] = safe_check(ch)
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
