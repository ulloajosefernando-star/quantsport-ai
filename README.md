# QuantSport AI

Plataforma de análisis cuantitativo deportivo. Calcula probabilidades, compara con las cuotas del mercado sin margen y solo señala una oportunidad cuando el modelo está validado y la evidencia es suficiente. En cualquier otro caso recomienda **NO BET**.

> **Aviso.** Es un análisis probabilístico, no una garantía de resultado. La plataforma no realiza apuestas, no deposita dinero, no inicia sesión en casas de apuestas ni ejecuta transacciones financieras. Un ROI histórico positivo no demuestra rentabilidad futura.

Estado actual: **Fase 1 completa, Fase 3 (datos reales de fútbol) en curso y Fase 7 (programador automático) lista**. El detalle de lo completo y lo pendiente, las fuentes, los costes en MXN y el plan por fases están en [docs/ARQUITECTURA_Y_PRESUPUESTO.md](docs/ARQUITECTURA_Y_PRESUPUESTO.md).

## Requisitos

- Python 3.12 o superior (probado con 3.13). En Mac, el `python3` del sistema suele ser 3.9: instala uno nuevo desde python.org o con `brew install python@3.13`; los scripts lo detectan solos
- Node 20 o superior (probado con 22)
- Opcional: Docker con Docker Compose

## Inicio rápido (modo local, coste 0)

```bash
cp .env.example .env          # sin claves funciona en modo demo
./scripts/dev.sh              # backend en :8000 y web en http://localhost:5173
```

O paso a paso:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8000      # crea la base y aplica migraciones al arrancar

cd ../frontend
npm install
npm run dev                                      # http://localhost:5173
```

La documentación interactiva de la API queda en http://localhost:8000/docs.

## Con Docker

```bash
cp .env.example .env
docker compose up -d --build                     # web en http://localhost:8080
```

El puerto se publica solo en `127.0.0.1`. Si lo expones en un VPS, define `APP_ENV=cloud` y un `ADMIN_TOKEN` en `.env`: sin token, los endpoints de escritura quedan bloqueados fuera del modo local.

## Modos de datos

- `DATA_MODE=demo` (por defecto): genera una liga **ficticia y sintética** de 16 equipos. Todo dato sintético aparece con la etiqueta DEMO. Sirve para ver la plataforma funcionando, no para apostar.
- `DATA_MODE=real`: solo usa proveedores configurados en `.env` y CSV importados. Al pasar a este modo, los datos sintéticos se eliminan.

## Liga MX con datos reales (gratis)

```bash
./scripts/validar_liga_mx.sh
```

El script hace todo en tu computadora:

1. Descarga el histórico de Liga MX de football-data.co.uk (resultados y cuotas de cierre).
2. Si pusiste `THE_ODDS_API_KEY` en `.env`, trae el calendario (no gasta créditos) y las cuotas actuales (unos 2 créditos).
3. Lista los equipos cuyos nombres no coinciden entre fuentes.
4. Ajusta el modelo, lo valida con walk-forward y guarda un informe en `data/reports/`.

Para ver esos datos en el dashboard, pon `DATA_MODE=real` en `.env` y reinicia el backend.

Qué esperar con datos gratuitos:

- El CSV de Liga MX solo trae cuotas de **cierre** de 1X2. El modelo se compara contra ese cierre, que es la referencia más difícil de superar.
- Over/Under y ambos anotan quedan como "experimental" hasta que haya cuotas históricas de esos mercados. La plataforma las va acumulando sola cada vez que sincronizas The Odds API.

## NBA (gratis, experimental)

1. Crea una clave gratuita en [balldontlie.io](https://www.balldontlie.io) (plan Free: partidos y marcadores, 5 consultas por minuto) y ponla en `.env` como `BALLDONTLIE_API_KEY`.
2. Descarga el historial y valida el modelo (tarda unos 15 minutos por el límite de consultas):

```bash
cd backend && .venv/bin/python -m app.cli nba-historial --temporadas 2022,2023,2024,2025
```

3. Para que el programador siga la NBA, agrega `nba` en `.env`: `SCHEDULER_COMPETITIONS=mex_liga_mx,nba`. Cada día baja resultados y calendario de balldontlie (gratis) y toma una foto de cuotas de The Odds API (ganador, hándicap y totales: 3 créditos con una región, unos 90 al mes).

El modelo NBA estima puntos de cada equipo con ratings de ataque y defensa, localía y más peso a los partidos recientes. No ve lesiones, descansos ni cambios de plantilla. No hay cuotas históricas gratuitas de la NBA, así que solo puede compararse con el mercado usando las cuotas que guarde el programador. Hasta reunir al menos 300 partidos con cuotas, todos sus mercados quedan en revisión y la recomendación es NO BET.

## Fuentes y sincronización

| Fuente | Cómo se usa | Costo |
|---|---|---|
| CSV football-data.co.uk | **Fuentes → Importar CSV**, o `python -m app.cli importar <ruta o URL>` | Gratis |
| The Odds API | `THE_ODDS_API_KEY`. Calendario (gratis) y cuotas (mercados × regiones créditos) | 500 créditos/mes gratis |
| football-data.org | `FOOTBALL_DATA_ORG_TOKEN`. Resultados y calendario de 12 ligas (sin Liga MX) | Gratis |
| openfootball | Sin clave. Resultados y calendario de Inglaterra (1.ª y 2.ª), España, Alemania, Italia y Francia; sirve para cruzar resultados entre fuentes (avisa si no coinciden) | Gratis, dominio público |
| Club Elo | Descarga el CSV en tu navegador (p. ej. `http://api.clubelo.com/2026-10-23`) y súbelo en **Importar CSV**. Se muestra como referencia externa en el detalle de cada partido; no entra en el modelo. Solo clubes europeos | Gratis. Importación manual porque su robots.txt no permite descargas automáticas |
| API-Football | `API_FOOTBALL_KEY`. Resultados, calendario y cuotas de Liga MX y 1,000+ ligas | 100 solicitudes/día gratis. **El plan gratuito solo da temporadas pasadas**; la actual requiere plan de pago (Pro 19 USD ≈ 350 MXN) |

Desde el dashboard: **Fuentes → Sincronizar proveedor**. Desde la terminal (en `backend/`):

```bash
.venv/bin/python -m app.cli sincronizar --proveedor api_football --competicion mex_liga_mx --tipo results --temporadas 2022,2023,2024
.venv/bin/python -m app.cli sincronizar --proveedor the_odds_api --competicion mex_liga_mx --tipo odds
.venv/bin/python -m app.cli equipos --competicion mex_liga_mx      # equipos sin historial y sugerencias
.venv/bin/python -m app.cli fusionar --de 41 --en 7                # une un equipo duplicado con el correcto
.venv/bin/python -m app.cli validar --competicion mex_liga_mx      # informe de validación
```

Cada llamada queda registrada en **Fuentes** con los créditos consumidos. La plataforma deja de llamar antes de pasarse del límite del plan, y los errores de plan no se reintentan.

**Nombres de equipos.** Cada fuente escribe los nombres distinto ("Club America", "America", "América"). Los alias conocidos están en `data/team_aliases.json`. Si aparece un equipo sin historial, ve a **Fuentes → Revisión de equipos** y fusiónalo con el correcto. El alias queda guardado para las siguientes sincronizaciones.

## Cuotas y mercados

La pantalla **Cuotas y mercados** muestra cada mercado con cuota (de cualquier fuente):
- Probabilidad implícita y margen.
- Probabilidad sin margen, con método proporcional, potencia o Shin.
- Probabilidad del modelo y cuota justa.
- Edge y EV.
- Incertidumbre y calidad de datos.

Filtra por deporte, competición, evento, mercado, rango de cuota, probabilidad implícita, EV y edge. Elige el formato de cuota: decimal, americano, fraccionario o porcentaje. Incluye una calculadora: escribes una cuota en cualquier formato y, si das tu probabilidad, te devuelve EV, cuota justa y stake.

Hay 70 mercados registrados en fútbol, NBA, NFL, MLB y tenis. Fútbol tiene modelo en 10 mercados y la NBA en 4 (ganador, hándicap, totales y totales por equipo, todos en revisión). En los demás la plataforma muestra implícita y margen, y dice que no hay modelo en vez de inventar una probabilidad. Fórmulas, ejemplos y cobertura: [docs/CUOTAS_Y_MERCADOS.md](docs/CUOTAS_Y_MERCADOS.md).

**Captura manual.** La plataforma no se conecta a ninguna casa de apuestas. Para evaluar una cuota que ves en cualquier casa o fuente, abre el partido en **Cartelera → detalle** y usa **Evaluar una cuota de cualquier casa o fuente**. La cuota queda guardada con origen `manual` y no entra en el consenso del mercado. Si la evidencia es débil, la respuesta sigue siendo NO BET.

## Uso diario en tu Mac (recomendado)

Una sola vez, desde la carpeta del proyecto:

```bash
./scripts/instalar_mac.sh
```

Con eso, el panel y el programador automático corren juntos en **http://localhost:8000** y arrancan solos cada vez que inicias sesión. Si algo falla, se reinician. Mientras la Mac esté conectada a la corriente no se duerme, para no perder tareas. El panel solo escucha en tu Mac y no queda abierto a internet. Necesita Python 3.12+ y Node.js (nodejs.org, versión LTS) solo para preparar el panel. Para quitarlo: `./scripts/desinstalar_mac.sh`. Registro: `data/logs/quantsport.log`.

Para actualizar a una versión nueva: `git pull` y vuelve a correr `./scripts/instalar_mac.sh`.

### Programador automático

Se activa en `.env`:

```bash
DATA_MODE=real
SCHEDULER_ENABLED=true
THE_ODDS_API_KEY=tu_clave_gratuita   # sin clave solo hace los pasos gratuitos
THE_ODDS_API_REGIONS=eu              # 1 región = 2 créditos por consulta de cuotas
```

Tareas (hora local, configurables con `SCHEDULER_*`):

- **Diaria (07:30):** descarga el CSV de football-data.co.uk, actualiza calendario y resultados, liquida pronósticos con su CLV y guarda la foto del ranking. Si la Mac estuvo apagada, se pone al día al arrancar.
- **Cuotas (09:00 y 17:00):** consulta 1X2 y totales.
- **Previa (cada 15 min):** 45 minutos antes de cada partido toma una última foto de cuotas y registra el pronóstico.

Cada tarea se ejecuta una sola vez por periodo, aunque reinicies. Antes de cada consulta de pago revisa el presupuesto mensual de créditos y deja una reserva (`SCHEDULER_CREDIT_RESERVE`). Si no alcanza, omite esa consulta y sigue con los pasos gratuitos. Estado, plan de créditos y botones para ejecutar a mano: **Fuentes de datos → Programador automático**, o `python -m app.cli tarea diaria|cuotas|previa`. Para un servidor Linux existe `scripts/instalar_servidor.sh` ([docs/SERVIDOR.md](docs/SERVIDOR.md)), opcional.

El programador nunca apuesta, no deposita y no inicia sesión en ninguna casa.

### Resultados pasados

En **Resultados** ves cada partido jugado con su marcador, la probabilidad del mercado al cierre (sin margen), la del modelo antes del partido y si acertó, y los pronósticos que registró el programador con su liquidación y CLV. Filtra por competición, equipo y fechas. El resumen compara el modelo con el mercado en el periodo elegido. La probabilidad del modelo es fuera de muestra: cada partido se predijo solo con partidos anteriores.

## Telegram

Crea un bot con @BotFather y define `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_IDS`. En **Configuración → Telegram** puedes ver la vista previa y enviar el resumen. Solo se envía a los chats autorizados y con un límite diario. Los comandos interactivos llegan en la Fase 6.

## Pantallas

Inicio, Cartelera, Oportunidades, Detalle de evento, Resultados, Rendimiento y validación, Fuentes de datos, Configuración (umbrales, competiciones, presupuesto, Telegram, token) y Herramientas (calculadora de cuotas, margen, EV y Kelly).

## Pruebas

```bash
./scripts/test.sh      # ruff + pytest (236 pruebas) + build del frontend
```

Ninguna prueba usa APIs reales ni consume cuotas: todo el HTTP se simula con `httpx.MockTransport`.

## Copias de seguridad

```bash
./scripts/backup.sh    # copia consistente de data/quantsport.db en data/backups (guarda las 14 más recientes)
```

## Seguridad

- Las claves solo viven en `.env`, que está en `.gitignore`. `.env.example` no contiene secretos.
- Los registros ocultan claves y tokens.
- Los endpoints de escritura requieren `X-Admin-Token` fuera del modo local.
- Las entradas (cuotas, CSV de hasta 10 MB, umbrales) se validan.

## Estructura

```
backend/   FastAPI, modelos, validación, motor de valor, proveedores, pruebas
frontend/  React + Vite + Tailwind (español, zona horaria America/Mexico_City)
data/      base SQLite, caché de proveedores, CSV importados (fuera de git)
docs/      arquitectura, fuentes, presupuesto y plan
scripts/   desarrollo, pruebas y backups
```
