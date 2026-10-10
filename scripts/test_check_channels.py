"""Pruebas del revisor de canales. Uso: python3 scripts/test_check_channels.py"""
import json, os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_channels as cc
from check_channels import NetError

FAILS = []
def ok(c, m):
    print(("OK  - " if c else "FALLA - ") + m)
    if not c: FAILS.append(m)
NOSLEEP = lambda s: None

# ---- paginas de ejemplo (estructura de youtube.com/channel/UC.../live) ----
DATA = '<script>var ytInitialData = {"contents":{}};</script>'
VID = "abcDEF12345"
LIVE_PAGE = '<link rel="canonical" href="https://www.youtube.com/watch?v=%s">' % VID + DATA + '<script>{"videoDetails":{"isLive":true,"isLiveContent":true}}</script>'
LIVE_NOW_FLAG = '<link rel="canonical" href="https://www.youtube.com/watch?v=%s">' % VID + DATA + '<script>{"liveBroadcastDetails":{"isLiveNow":true}}</script>'
ENDED_STREAM = '<link rel="canonical" href="https://www.youtube.com/watch?v=%s">' % VID + DATA + '<script>{"videoDetails":{"isLiveContent":true}}</script>'
CHANNEL_PAGE = '<link rel="canonical" href="https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv">' + DATA
HANDLE_PAGE = '<link rel="canonical" href="https://www.youtube.com/@alguien">' + DATA
# Variantes reales: la pagina que ve un servidor puede no traer el enlace canonical
NO_CANON_LIVE = DATA + '<script>{"videoDetails":{"videoId":"%s","title":"EN VIVO","isLive":true,"isLiveContent":true}}</script>' % VID
CANON_REVERSED = '<link href="https://www.youtube.com/watch?v=%s" rel="canonical">' % VID + DATA + '<script>{"videoDetails":{"isLive":true}}</script>'
LIVE_NO_ID = DATA + '<script>{"videoDetails":{"isLive":true}}</script>'
CONSENT = '<html><body><form action="https://consent.youtube.com/save">Antes de ir a YouTube</form></body></html>'

ok(cc.analyze_live_page(LIVE_PAGE) == ("live", VID), "transmitiendo ahora (isLive) -> 'live' con el id del video")
ok(cc.analyze_live_page(LIVE_NOW_FLAG) == ("live", VID), "transmitiendo ahora (isLiveNow) -> 'live'")
ok(cc.analyze_live_page(CHANNEL_PAGE) == ("offline", "") and cc.analyze_live_page(HANDLE_PAGE) == ("offline", ""), "pagina del canal sin transmision -> offline")
ok(cc.analyze_live_page(NO_CANON_LIVE) == ("live", VID), "sin canonical pero con videoDetails.videoId e isLive -> 'live' (lo que ve GitHub)")
ok(cc.analyze_live_page(CANON_REVERSED) == ("live", VID), "canonical con los atributos en otro orden -> 'live'")
k, why = cc.analyze_live_page(LIVE_NO_ID)
ok(k == "unknown" and "videoDetails=0" in why and "isLive=1" in why, "en vivo pero sin poder ubicar el video -> unknown con marcas: %s" % why)
k, why = cc.analyze_live_page(ENDED_STREAM)
ok(k == "unknown" and "isLiveContent=1" in why and "canonical=watch" in why, "una transmision que ya termino NO se da por 'en vivo' y explica las marcas: %s" % why)
k, why = cc.analyze_live_page(CONSENT)
ok(k == "unknown" and "consentimiento" in why, "pantalla de consentimiento -> unknown (nunca 'offline')")
ok(cc.analyze_live_page("")[0] == "unknown", "pagina vacia -> unknown")

for code, want in [(200, "ok"), (301, "ok"), (302, "ok"), (404, "down"), (410, "down"), (500, "unknown"), (503, "unknown"), (403, "unknown"), (401, "unknown"), (429, "unknown"), (418, "unknown")]:
    ok(cc.classify_http(code) == want, "HTTP %s -> %s" % (code, want))

# ---- oEmbed ----
def fake(pages):
    def get(url, headers=None):
        for k, v in pages.items():
            if k in url:
                if isinstance(v, Exception): raise v
                return v
        raise NetError("other")
    return get
for code, want in [(200, "ok"), (401, "noembed"), (404, "offline"), (403, "unknown"), (500, "unknown")]:
    ok(cc.oembed_state(VID, fake({"oembed": (code, "")}), NOSLEEP)[0] == want, "oEmbed %s -> %s" % (code, want))
ok(cc.oembed_state(VID, fake({"oembed": NetError("timeout")}), NOSLEEP)[0] == "unknown", "oEmbed sin respuesta -> unknown")
asked = []
cc.oembed_state(VID, lambda u, h=None: (asked.append(u), (200, ""))[1], NOSLEEP)
ok("watch%3Fv%3D" + VID in asked[0] and asked[0].startswith("https://www.youtube.com/oembed?"), "oEmbed pregunta por el video correcto: %s" % asked[0])

# ---- check_channel ----
YT_LIVE = {"id": "a", "url": "https://www.youtube.com/embed/live_stream?channel=UCabcdefghijklmnopqrstuv&autoplay=1", "domain": "youtube.com"}
LIVE_URL = "/channel/UCabcdefghijklmnopqrstuv/live"
ok(cc.check_channel(YT_LIVE, fake({LIVE_URL: (200, LIVE_PAGE), "oembed": (200, "{}")}), NOSLEEP)[0] == "ok", "canal en vivo y con integracion permitida -> ok")
ok(cc.check_channel(YT_LIVE, fake({LIVE_URL: (200, LIVE_PAGE), "oembed": (401, "")}), NOSLEEP)[0] == "noembed", "canal en vivo pero con integracion desactivada -> noembed")
ok(cc.check_channel(YT_LIVE, fake({LIVE_URL: (200, CHANNEL_PAGE)}), NOSLEEP)[0] == "offline", "canal sin transmision -> offline")
ok(cc.check_channel(YT_LIVE, fake({LIVE_URL: (200, CONSENT)}), NOSLEEP)[0] == "unknown", "consentimiento -> unknown")
ok(cc.check_channel(YT_LIVE, fake({LIVE_URL: (429, "")}), NOSLEEP) == ("unknown", "YouTube respondio 429"), "YouTube responde 429 -> unknown")
ok(cc.check_channel(YT_LIVE, fake({LIVE_URL: NetError("timeout")}), NOSLEEP)[0] == "unknown", "sin red hacia YouTube -> unknown")
YT_VID = {"id": "v", "url": "https://www.youtube.com/embed/3JM61yuIzS4?autoplay=1", "domain": "youtube.com"}
ok(cc.check_channel(YT_VID, fake({"oembed": (200, "{}")}), NOSLEEP)[0] == "ok", "video fijo que permite integrarse -> ok")
ok(cc.check_channel(YT_VID, fake({"oembed": (401, "")}), NOSLEEP)[0] == "noembed", "video fijo con integracion desactivada -> noembed")
ok(cc.check_channel(YT_VID, fake({"oembed": (404, "")}), NOSLEEP) == ("offline", "video no disponible"), "video fijo borrado o privado -> offline 'video no disponible'")
YT_LIST = {"id": "pl", "url": "https://www.youtube.com/embed/videoseries?list=PLabcdefghijkl&autoplay=1", "domain": "youtube.com"}
asked_l = []
def spy(url, headers=None):
    asked_l.append(url); return 200, "{}"
ok(cc.check_channel(YT_LIST, spy, NOSLEEP)[0] == "ok" and "playlist%3Flist%3DPLabcdefghijkl" in asked_l[0], "lista de reproduccion: se pregunta por la LISTA (no por un video llamado 'videoseries'): %s" % asked_l)
ok(cc.check_channel(YT_LIST, fake({"oembed": (404, "")}), NOSLEEP)[0] == "offline", "lista borrada o privada -> offline")
ok(cc.check_channel(YT_LIST, fake({"oembed": (401, "")}), NOSLEEP)[0] == "noembed", "lista con integracion desactivada -> noembed")
ok(cc.uses_youtube_page(YT_LIST), "las listas se revisan de a uno con pausa, como el resto de YouTube")
ok(not cc.uses_youtube_page({"url": "https://www.youtube.com/embed/videoseries?x=1", "domain": "youtube.com"}), "y un 'videoseries' sin lista no se confunde con un video")
DIRECT = {"id": "d", "url": "https://www.mega.cl/senal", "direct": True}
ok(cc.check_channel(DIRECT, fake({"mega.cl": (200, "")}), NOSLEEP)[0] == "ok", "sitio que abre en pestana y responde -> ok")
ok(cc.check_channel(DIRECT, fake({"mega.cl": (404, "")}), NOSLEEP)[0] == "down", "sitio con 404 -> down")
ok(cc.check_channel(DIRECT, fake({"mega.cl": (403, "")}), NOSLEEP)[0] == "unknown", "sitio que bloquea robots (403) -> unknown")
ok(cc.check_channel(DIRECT, fake({"mega.cl": NetError("dns")}), NOSLEEP)[0] == "down", "dominio que ya no existe -> down")
ok(cc.check_channel(DIRECT, fake({"mega.cl": NetError("timeout")}), NOSLEEP)[0] == "unknown", "tiempo agotado -> unknown (no se culpa al canal)")
YT_AS_TAB = {"id": "t", "url": "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/live", "direct": True}
ok(cc.check_channel(YT_AS_TAB, fake({"youtube.com/channel": (200, "")}), NOSLEEP)[0] == "ok", "canal de YouTube que abre en pestana: solo se mira que el sitio responda")

# ---- 429 de YouTube: reintentos con espera ----
calls = []; sleeps = []
def flaky(n429):
    def get(url, headers=None):
        calls.append(url)
        return (429, "") if len(calls) <= n429 else (200, "{}")
    return get
r = cc.check_channel(YT_VID, flaky(2), sleep=sleeps.append)
ok(r[0] == "ok" and len(calls) == 3 and sleeps == [15, 30], "dos 429 y luego 200: reintenta con espera creciente y termina en ok (esperas %s)" % sleeps)
calls.clear(); sleeps.clear()
r = cc.check_channel(YT_VID, flaky(99), sleep=sleeps.append)
ok(r[0] == "unknown" and "429" in r[1] and len(calls) == 3, "si siempre responde 429: 3 intentos y queda 'unknown' con su razon")
calls.clear(); sleeps.clear()
cc.check_channel(DIRECT, flaky(99), sleep=sleeps.append)
ok(len(calls) == 1 and not sleeps, "los sitios que no son YouTube no se reintentan ni esperan")

# ---- merge: dias seguidos con problema (por fecha) ----
D = lambda n, h=6: datetime(2026, 10, 10, h, 0, tzinfo=timezone.utc) + timedelta(days=n)
def step(old, state, day, url="u1", h=6):
    return {"channels": cc.merge([{"id": "x", "url": url}], {"x": (state, "")}, old, D(day, h))}
e = lambda s: s["channels"]["x"]
s = step(None, "offline", 0); ok(e(s)["fails"] == 1 and e(s)["since"] == "2026-10-10", "primer dia con problema: 1")
s = step(s, "offline", 1); ok(e(s)["fails"] == 2, "segundo dia seguido: 2")
s = step(s, "offline", 1, h=18); ok(e(s)["fails"] == 2, "otra ejecucion el MISMO dia no suma (sigue en 2)")
s = step(s, "unknown", 2); ok(e(s)["state"] == "unknown" and e(s)["fails"] == 3 and e(s)["badState"] == "offline", "un dia sin datos: no se muestra estado, pero la cuenta sigue (3)")
s = step(s, "offline", 3); ok(e(s)["state"] == "offline" and e(s)["fails"] == 4 and "badState" not in e(s), "al volver el mismo problema la cuenta continua (4)")
s = step(s, "ok", 4); ok(e(s)["fails"] == 0 and "since" not in e(s), "cuando vuelve a funcionar: 0 y se olvida")
s = step(s, "unknown", 5); ok(e(s)["fails"] == 0 and "badState" not in e(s), "ok -> unknown no inventa problemas")
s = step(None, "down", 0); s = step(s, "offline", 1); ok(e(s)["fails"] == 1 and e(s)["since"] == "2026-10-11", "cambiar de tipo de problema reinicia la cuenta")
s = step(None, "down", 0); s = step(s, "unknown", 1); s = step(s, "offline", 2); ok(e(s)["fails"] == 1, "un problema distinto despues de un dia sin datos tambien reinicia")
s = step(None, "noembed", 0); s = step(s, "noembed", 1); s2 = step(s, "noembed", 2, url="u2")
ok(e(s)["fails"] == 2 and e(s2)["fails"] == 1 and e(s2)["url"] == "u2", "si el canal se edita (otra direccion) la cuenta empieza de cero")
ok(set(cc.merge([{"id": "a", "url": "1"}], {"a": ("ok", "")}, {"channels": {"borrado": {"state": "down", "fails": 9, "url": "z"}}}, D(0))) == {"a"}, "los canales eliminados desaparecen de status.json")

# ---- should_write ----
now = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)
chans = {"x": {"state": "ok", "fails": 0, "url": "u"}}
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
ok(cc.should_write(chans, None, now), "primera vez: escribe")
ok(not cc.should_write(chans, {"checkedAt": iso(now - timedelta(days=1)), "channels": chans}, now), "sin cambios y reciente: NO escribe (evita commits diarios vacios)")
ok(cc.should_write(chans, {"checkedAt": iso(now - timedelta(days=4)), "channels": chans}, now), "sin cambios pero con 4 dias: escribe (mantiene viva la revision)")
ok(cc.should_write({"x": {"state": "down", "fails": 1, "url": "u"}}, {"checkedAt": iso(now), "channels": chans}, now), "con cambios: escribe")
ok(cc.should_write(chans, {"checkedAt": "basura", "channels": chans}, now), "fecha ilegible: escribe")

# ---- main de punta a punta ----
with tempfile.TemporaryDirectory() as d:
    channels = [
        {"id": "dw", "name": "DW", "url": "https://www.youtube.com/embed/live_stream?channel=UCabcdefghijklmnopqrstuv&autoplay=1"},
        {"id": "mega", "name": "Mega", "url": "https://www.mega.cl/", "direct": True},
        {"id": "roto", "name": "Roto", "url": "https://sitio-que-no-existe.example/", "direct": True},
        {"id": "malo", "name": "Malo", "url": "https://explota.example/", "direct": True},
    ]
    json.dump(channels, open(os.path.join(d, "channels.json"), "w"))
    before = open(os.path.join(d, "channels.json")).read()
    def get(url, headers=None):
        if "explota" in url: raise ValueError("fallo raro")
        if "oembed" in url: return 401, ""
        if "/live" in url: return 200, LIVE_PAGE
        if "mega.cl" in url: return 200, ""
        raise NetError("dns")
    wrote = cc.main(d, get, now, sleep=NOSLEEP)
    st = json.load(open(os.path.join(d, "status.json")))
    ok(wrote and st["checkedAt"] == "2026-10-10T06:00:00Z", "escribe status.json con la fecha de la revision")
    ok([st["channels"][k]["state"] for k in ("dw", "mega", "roto", "malo")] == ["noembed", "ok", "down", "unknown"], "estados correctos para cada canal: %s" % {k: v["state"] for k, v in st["channels"].items()})
    ok("error interno" in st["channels"]["malo"].get("detail", ""), "un canal que provoca un error no frena la revision de los demas")
    ok(open(os.path.join(d, "channels.json")).read() == before, "channels.json NO se modifica nunca")
    wrote2 = cc.main(d, get, now + timedelta(days=1), sleep=NOSLEEP)
    st2 = json.load(open(os.path.join(d, "status.json")))
    ok(wrote2 is True and st2["channels"]["roto"]["fails"] == 2 and st2["channels"]["dw"]["fails"] == 2, "al dia siguiente cuenta 2 dias seguidos")
    ok(cc.main(d, get, now + timedelta(days=1, hours=1), sleep=NOSLEEP) is False, "misma situacion una hora despues: no vuelve a escribir")
    open(os.path.join(d, "status.json"), "w").write("{esto no es json")
    ok(cc.main(d, get, now, sleep=NOSLEEP) is True and json.load(open(os.path.join(d, "status.json")))["channels"], "si status.json esta corrupto lo rehace sin romperse")

# ---- pausas, corta-circuitos y tope de tiempo ----
yts = [{"id": "y%d" % i, "name": "YT%d" % i, "url": "https://www.youtube.com/embed/live_stream?channel=UC%022d&autoplay=1" % i} for i in range(10)]
with tempfile.TemporaryDirectory() as d:
    json.dump(yts[:4] + [{"id": "w", "name": "Web", "url": "https://sitio.example/", "direct": True}], open(os.path.join(d, "channels.json"), "w"))
    order = []
    def get2(url, headers=None):
        order.append(url); return (200, LIVE_PAGE) if "/live" in url else (200, "{}")
    pauses = []
    cc.main(d, get2, now, sleep=pauses.append)
    ok(len(pauses) == 3 and all(cc.YT_DELAY <= x < cc.YT_DELAY + 1 for x in pauses), "4 canales de YouTube -> 3 pausas de ~2 s entre ellos: %s" % [round(x, 1) for x in pauses])
with tempfile.TemporaryDirectory() as d:
    json.dump(yts, open(os.path.join(d, "channels.json"), "w"))
    reqs = []
    def blocked(url, headers=None):
        reqs.append(url); return 429, ""
    cc.main(d, blocked, now, sleep=NOSLEEP)
    st = json.load(open(os.path.join(d, "status.json")))["channels"]
    ok(len(reqs) == cc.YT_MAX_BLOCKED * cc.YT_RETRIES, "si YouTube bloquea 3 canales seguidos deja de insistir: %d pedidos en vez de %d" % (len(reqs), 10 * cc.YT_RETRIES))
    ok(all(v["state"] == "unknown" for v in st.values()) and "limita" in st["y9"]["detail"], "los demas quedan 'unknown' con la razon: %r" % st["y9"]["detail"])
with tempfile.TemporaryDirectory() as d:
    json.dump(yts, open(os.path.join(d, "channels.json"), "w"))
    tick = iter(range(0, 100000, 200))
    reqs2 = []
    def fine(url, headers=None):
        reqs2.append(url); return (200, LIVE_PAGE) if "/live" in url else (200, "{}")
    cc.main(d, fine, now, sleep=NOSLEEP, clock=lambda: next(tick))
    st = json.load(open(os.path.join(d, "status.json")))["channels"]
    ok(0 < len(reqs2) < 20 and any("agoto" in v.get("detail", "") for v in st.values()), "tope de tiempo: revisa solo los que alcanza y explica el resto (%d pedidos)" % len(reqs2))
with tempfile.TemporaryDirectory() as d:
    json.dump(yts[:5], open(os.path.join(d, "channels.json"), "w"))
    calls3 = []
    def mixed(url, headers=None):
        calls3.append(url)
        if len(calls3) <= 3: return 429, ""
        return (200, LIVE_PAGE) if "/live" in url else (200, "{}")
    cc.main(d, mixed, now, sleep=NOSLEEP)
    st = json.load(open(os.path.join(d, "status.json")))["channels"]
    got = [st["y%d" % i]["state"] for i in range(5)]
    ok(got == ["unknown", "ok", "ok", "ok", "ok"], "un canal bloqueado una vez queda sin dato y los demas siguen normal: %s" % got)

print("\n" + ("%d FALLARON: %s" % (len(FAILS), FAILS) if FAILS else "TODO OK"))
raise SystemExit(1 if FAILS else 0)
