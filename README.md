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

Pruebas del revisor: `python3 scripts/test_check_channels.py`.
