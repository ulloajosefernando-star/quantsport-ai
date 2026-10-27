#!/usr/bin/env bash
# Deja QuantSport AI funcionando en tu Mac: panel en http://localhost:8000 y programador automático.
# Arranca solo al iniciar sesión y se reinicia si falla. Solo escucha en tu Mac (127.0.0.1).
# Uso:  ./scripts/instalar_mac.sh        Para quitarlo:  ./scripts/desinstalar_mac.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.quantsport.ai
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$ROOT/data/logs"

if [ "$(uname)" != "Darwin" ]; then
  echo "Este instalador es para macOS. En un servidor Linux usa scripts/instalar_servidor.sh." >&2
  exit 1
fi
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Creé .env desde .env.example (modo demo). Para datos reales pon DATA_MODE=real y tu THE_ODDS_API_KEY."
fi
chmod 600 "$ROOT/.env"
grep -qE '^DATA_MODE=real' "$ROOT/.env" || echo "Aviso: .env no tiene DATA_MODE=real; el panel abrirá en modo demo y el programador no correrá."
grep -qE '^SCHEDULER_ENABLED=true' "$ROOT/.env" || echo "Aviso: .env no tiene SCHEDULER_ENABLED=true; el programador automático quedará apagado."

if ! command -v npm >/dev/null 2>&1; then
  echo "Falta Node.js para preparar el panel. Instálalo desde https://nodejs.org (versión LTS) y vuelve a correr este script." >&2
  exit 1
fi

echo "1/4 Preparando Python…"
cd "$ROOT/backend"
# shellcheck source=_venv.sh
source "$ROOT/scripts/_venv.sh"
ensure_venv requirements.txt
.venv/bin/alembic upgrade head >/dev/null

echo "2/4 Preparando el panel…"
cd "$ROOT/frontend"
[ -d node_modules ] || npm install --silent
npm run build --silent >/dev/null

echo "3/4 Deteniendo versiones anteriores…"
if [ -f "$ROOT/programador.pid" ]; then
  kill "$(cat "$ROOT/programador.pid")" 2>/dev/null || true
  rm -f "$ROOT/programador.pid"
fi
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

echo "4/4 Registrando el arranque automático…"
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
chmod +x "$ROOT/scripts/arrancar_mac.sh"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/arrancar_mac.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/quantsport.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/quantsport.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$PLIST"

for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  sleep 2
done
if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo
  echo "Listo: QuantSport AI está corriendo en http://localhost:8000"
  echo "Arranca solo cada vez que inicias sesión. Registro: $LOG_DIR/quantsport.log"
  open http://localhost:8000 || true
else
  echo "No respondió a tiempo. Revisa el registro: tail -n 50 $LOG_DIR/quantsport.log" >&2
  exit 1
fi
