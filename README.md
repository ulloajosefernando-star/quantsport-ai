# QuantSport AI

Plataforma de análisis cuantitativo deportivo. Calcula probabilidades, compara con las cuotas del mercado sin margen y solo señala una oportunidad cuando el modelo está validado y la evidencia es suficiente. En cualquier otro caso recomienda **NO BET**.

> **Aviso.** Es un análisis probabilístico, no una garantía de resultado. La plataforma no realiza apuestas, no deposita dinero, no inicia sesión en casas de apuestas ni ejecuta transacciones financieras. Un ROI histórico positivo no demuestra rentabilidad futura.

Estado actual: **Fase 1**. El detalle de lo completo y lo pendiente, las fuentes, los costes en MXN y el plan por fases están en [docs/ARQUITECTURA_Y_PRESUPUESTO.md](docs/ARQUITECTURA_Y_PRESUPUESTO.md).

## Requisitos

- Python 3.12 o superior (probado con 3.13)
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

## Cargar datos reales gratis

1. **CSV de football-data.co.uk.** Descarga, por ejemplo, `https://www.football-data.co.uk/new/MEX.csv` (Liga MX) desde tu navegador y súbelo en **Fuentes → Importar CSV**. También acepta el formato principal de ligas europeas y un formato genérico (`date, competition, home, away, home_goals, away_goals[, odds_home, odds_draw, odds_away, bookmaker, odds_captured_at]`).
2. **The Odds API** (500 créditos/mes gratis): crea una clave en the-odds-api.com, ponla en `THE_ODDS_API_KEY` y sincroniza desde la API: `POST /api/providers/the_odds_api/sync?competition=mex_liga_mx&kind=odds` (con el encabezado `X-Admin-Token` si lo definiste).
3. **football-data.org** (gratis, 10 llamadas/min): pon el token en `FOOTBALL_DATA_ORG_TOKEN` y usa `POST /api/providers/football_data_org/sync?competition=eng_premier_league&kind=results`.

Cada llamada queda registrada en **Fuentes** con los créditos consumidos. La plataforma deja de llamar cuando se acerca al límite del plan.

## Telegram

Crea un bot con @BotFather y define `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_IDS`. En **Configuración → Telegram** puedes ver la vista previa y enviar el resumen. Solo se envía a los chats autorizados y con un límite diario. Los comandos interactivos llegan en la Fase 6.

## Pantallas

Inicio, Cartelera, Oportunidades, Detalle de evento, Rendimiento y validación, Fuentes de datos, Configuración (umbrales, competiciones, presupuesto, Telegram, token) y Herramientas (calculadora de cuotas, margen, EV y Kelly).

## Pruebas

```bash
./scripts/test.sh      # ruff + pytest (107 pruebas) + build del frontend
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
