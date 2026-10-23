#!/usr/bin/env bash
# Valida el modelo con datos REALES de Liga MX (gratis) en tu computadora:
#   1. descarga el histórico de football-data.co.uk (resultados y cuotas de cierre),
#   2. si tienes THE_ODDS_API_KEY en .env, trae el calendario (gratis) y las cuotas actuales (2 créditos),
#   3. lista equipos con nombres que no coinciden entre fuentes,
#   4. ajusta el modelo, ejecuta el walk-forward y guarda el informe en data/reports/.
# Usa DATA_MODE=real: los datos sintéticos del demo se eliminan de la base.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
export DATA_MODE=real
PY=.venv/bin/python

$PY -m app.cli importar https://www.football-data.co.uk/new/MEX.csv
if grep -qE '^THE_ODDS_API_KEY=.+' "$ROOT/.env" 2>/dev/null; then
  $PY -m app.cli sincronizar --proveedor the_odds_api --competicion mex_liga_mx --tipo fixtures || true
  $PY -m app.cli sincronizar --proveedor the_odds_api --competicion mex_liga_mx --tipo odds || true
else
  echo "Sin THE_ODDS_API_KEY: se valida solo con históricos (no habrá partidos próximos que analizar)."
fi
$PY -m app.cli equipos --competicion mex_liga_mx
$PY -m app.cli validar --competicion mex_liga_mx
echo
echo "Para ver estos datos en el dashboard, pon DATA_MODE=real en .env y reinicia el backend."
