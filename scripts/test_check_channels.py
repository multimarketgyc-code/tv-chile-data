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

# ---- paginas de ejemplo (estructura de YouTube) ----
DATA = '<script>var ytInitialData = {"contents":{}};</script>'
LIVE_EMBED = DATA + '<script>var ytInitialPlayerResponse = {"playabilityStatus":{"status":"OK","playableInEmbed":true},"microformat":{"playerMicroformatRenderer":{"liveBroadcastDetails":{"isLiveNow":true}}}};</script>'
LIVE_NOEMBED = LIVE_EMBED.replace('"playableInEmbed":true', '"playableInEmbed":false')
LIVE_NO_FLAG = DATA + '<script>var ytInitialPlayerResponse = {"microformat":{"x":{"liveBroadcastDetails":{"isLiveNow":true}}}};</script>'
NOT_LIVE = DATA + '<script>var ytInitialPlayerResponse = {"playabilityStatus":{"status":"OK","playableInEmbed":true}};</script>'
AMBIGUOUS = DATA + '<script>{"videoDetails":{"isLiveContent":true,"isLive":true}}</script>'
CONSENT = '<html><body><form action="https://consent.youtube.com/save">Antes de ir a YouTube</form></body></html>'

ok(cc.parse_live_page(LIVE_EMBED) == "ok", "en vivo y con integracion permitida -> ok")
ok(cc.parse_live_page(LIVE_NOEMBED) == "noembed", "en vivo pero YouTube no permite integrarlo -> noembed")
ok(cc.parse_live_page(LIVE_NO_FLAG) == "unknown", "en vivo pero sin dato de integracion -> unknown (no se afirma nada)")
ok(cc.parse_live_page(NOT_LIVE) == "offline", "pagina bien cargada sin transmision en vivo -> offline")
ok(cc.parse_live_page(AMBIGUOUS) == "unknown", "senales ambiguas -> unknown")
ok(cc.parse_live_page(CONSENT) == "unknown", "pantalla de consentimiento -> unknown (nunca 'offline')")
ok(cc.parse_live_page("") == "unknown", "pagina vacia -> unknown")
ok(cc.parse_video_page('{"playableInEmbed":true}') == "ok" and cc.parse_video_page('{"playableInEmbed":false}') == "noembed", "video individual: integrable / no integrable")
ok(cc.parse_video_page('{"playabilityStatus":{"status":"ERROR","reason":"Video no disponible"}}') == "offline", "video borrado -> offline")
ok(cc.parse_video_page(CONSENT) == "unknown", "video: consentimiento -> unknown")

for code, want in [(200, "ok"), (301, "ok"), (302, "ok"), (404, "down"), (410, "down"), (500, "down"), (503, "down"), (403, "unknown"), (401, "unknown"), (429, "unknown"), (418, "unknown")]:
    ok(cc.classify_http(code) == want, "HTTP %s -> %s" % (code, want))

# ---- check_channel con red simulada ----
def fake(pages):
    def get(url, headers=None):
        for k, v in pages.items():
            if k in url:
                if isinstance(v, Exception): raise v
                return v
        raise NetError("other")
    return get
YT_LIVE = {"id": "a", "url": "https://www.youtube.com/embed/live_stream?channel=UCabcdefghijklmnopqrstuv&autoplay=1", "domain": "youtube.com"}
ok(cc.check_channel(YT_LIVE, fake({"/channel/UCabcdefghijklmnopqrstuv/live": (200, LIVE_EMBED)}))[0] == "ok", "canal en vivo integrable")
ok(cc.check_channel(YT_LIVE, fake({"/live": (200, LIVE_NOEMBED)}))[0] == "noembed", "canal en vivo no integrable")
ok(cc.check_channel(YT_LIVE, fake({"/live": (200, NOT_LIVE)}))[0] == "offline", "canal sin transmision")
ok(cc.check_channel(YT_LIVE, fake({"/live": (429, "")}))[0] == "unknown", "YouTube responde 429 -> unknown")
ok(cc.check_channel(YT_LIVE, fake({"/live": NetError("timeout")}))[0] == "unknown", "sin red hacia YouTube -> unknown")
YT_VID = {"id": "v", "url": "https://www.youtube.com/embed/3JM61yuIzS4?autoplay=1", "domain": "youtube.com"}
ok(cc.check_channel(YT_VID, fake({"watch?v=3JM61yuIzS4": (200, '{"playableInEmbed":true}')}))[0] == "ok", "video individual integrable")
DIRECT = {"id": "d", "url": "https://www.mega.cl/senal", "direct": True}
ok(cc.check_channel(DIRECT, fake({"mega.cl": (200, "")}))[0] == "ok", "sitio que abre en pestana y responde -> ok")
ok(cc.check_channel(DIRECT, fake({"mega.cl": (404, "")}))[0] == "down", "sitio con 404 -> down")
ok(cc.check_channel(DIRECT, fake({"mega.cl": (403, "")}))[0] == "unknown", "sitio que bloquea robots (403) -> unknown")
ok(cc.check_channel(DIRECT, fake({"mega.cl": NetError("dns")}))[0] == "down", "dominio que ya no existe -> down")
ok(cc.check_channel(DIRECT, fake({"mega.cl": NetError("timeout")}))[0] == "unknown", "tiempo agotado -> unknown (no se culpa al canal)")
YT_AS_TAB = {"id": "t", "url": "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/live", "direct": True}
ok(cc.check_channel(YT_AS_TAB, fake({"youtube.com/channel": (200, "")}))[0] == "ok", "canal de YouTube que abre en pestana: solo se mira que el sitio responda")

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
        if "/live" in url: return 200, LIVE_NOEMBED
        if "mega.cl" in url: return 200, ""
        raise NetError("dns")
    wrote = cc.main(d, get, now)
    st = json.load(open(os.path.join(d, "status.json")))
    ok(wrote and st["checkedAt"] == "2026-10-10T06:00:00Z", "escribe status.json con la fecha de la revision")
    ok([st["channels"][k]["state"] for k in ("dw", "mega", "roto", "malo")] == ["noembed", "ok", "down", "unknown"], "estados correctos para cada canal: %s" % {k: v["state"] for k, v in st["channels"].items()})
    ok("error interno" in st["channels"]["malo"].get("detail", ""), "un canal que provoca un error no frena la revision de los demas")
    ok(open(os.path.join(d, "channels.json")).read() == before, "channels.json NO se modifica nunca")
    wrote2 = cc.main(d, get, now + timedelta(days=1))
    st2 = json.load(open(os.path.join(d, "status.json")))
    ok(wrote2 is True and st2["channels"]["roto"]["fails"] == 2 and st2["channels"]["dw"]["fails"] == 2, "al dia siguiente cuenta 2 dias seguidos (hay cambio en la cuenta, asi que escribe)")
    wrote3 = cc.main(d, get, now + timedelta(days=1, hours=1))
    ok(wrote3 is False, "misma situacion una hora despues: no vuelve a escribir")
    os.remove(os.path.join(d, "status.json"))
    open(os.path.join(d, "status.json"), "w").write("{esto no es json")
    ok(cc.main(d, get, now) is True and json.load(open(os.path.join(d, "status.json")))["channels"], "si status.json esta corrupto lo rehace sin romperse")

print("\n" + ("%d FALLARON: %s" % (len(FAILS), FAILS) if FAILS else "TODO OK"))
raise SystemExit(1 if FAILS else 0)
