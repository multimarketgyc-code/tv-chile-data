# tv-chile-data

Datos de **TV Chile En Vivo** (el codigo vive en el repositorio privado `tv-chile-portal`).

| Archivo | Que es | Quien lo escribe |
|---|---|---|
| `channels.json` | Lista de canales | El panel de administrador de la pagina |
| `devices.json` | Dispositivos autorizados | La pagina (al entrar con la clave) y el panel |
| `status.json` | Resultado de la revision diaria de canales | El flujo `Revisar canales` (GitHub Actions) |

## Revision diaria de canales

`.github/workflows/check-channels.yml` corre todos los dias (y a mano desde la pestana *Actions* → *Revisar canales* → *Run workflow*).
Para cada canal comprueba:
- **En vivo aqui (YouTube):** si esta transmitiendo ahora y si YouTube permite verlo dentro de otra pagina.
- **Abre en pestana:** si el sitio responde.

El resultado se muestra en el panel de administrador junto a cada canal. Reglas:
- Nunca modifica `channels.json`; solo escribe `status.json`.
- Ante la duda el estado es `unknown` y la pagina no muestra nada. Un canal solo se marca con problema cuando hay evidencia clara.
- Los dias con problema se cuentan por fecha. `status.json` solo se reescribe si algo cambia, o cada 3 dias como senal de vida.

Como se comprueba con YouTube: se usa **oEmbed** (la consulta oficial que dice si un video existe y permite integrarse: 200 si, 401 integracion desactivada, 404 no existe). YouTube se consulta de a uno, con pausas y reintentos, porque desde un servidor responde 429 si se le pide en paralelo.

Limitaciones conocidas:
- Los canales de YouTube por *canal* (`.../embed/live_stream?channel=...`) no siempre se pueden confirmar: la pagina que YouTube entrega a GitHub no trae el identificador del video en vivo. Quedan como `unknown` (sin dato) y el detalle de `status.json` explica por que.
- Varios sitios (ej. Paramount+) rechazan a los robots con 403: tambien quedan `unknown`.
- Un video fijo que sigue existiendo se marca `ok` aunque su transmision en vivo ya haya terminado (oEmbed no informa si esta en vivo).

Pruebas del revisor: `python3 scripts/test_check_channels.py`.
