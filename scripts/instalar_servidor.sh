#!/usr/bin/env bash
# Instala el programador automático como servicio en un servidor Linux (Ubuntu/Debian, x86 o ARM).
# Uso, desde la carpeta del repo ya clonado:  ./scripts/instalar_servidor.sh
# Solo corre el programador: no abre puertos ni publica el panel. Para ver el panel usa un túnel SSH.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE=quantsport-programador

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Este script es para Ubuntu o Debian." >&2
  exit 1
fi
echo "Instalando paquetes del sistema…"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git sqlite3 >/dev/null

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
  echo "Creé .env desde .env.example. Edítalo (nano $ROOT/.env) con DATA_MODE=real,"
  echo "SCHEDULER_ENABLED=true, THE_ODDS_API_KEY y THE_ODDS_API_REGIONS=eu, y vuelve a correr este script."
  exit 1
fi
chmod 600 "$ROOT/.env"
if ! grep -qE '^DATA_MODE=real' "$ROOT/.env"; then
  echo "Falta DATA_MODE=real en .env: el programador no corre en modo demo." >&2
  exit 1
fi

cd "$ROOT/backend"
# shellcheck source=_venv.sh
source "$ROOT/scripts/_venv.sh"
ensure_venv requirements.txt
.venv/bin/alembic upgrade head

sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<UNIT
[Unit]
Description=QuantSport AI · programador automático
After=network-online.target
Wants=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$ROOT/backend
ExecStart=$ROOT/backend/.venv/bin/python -m app.cli programador
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE
sleep 5
sudo systemctl --no-pager status $SERVICE | head -n 5
echo
echo "Listo. Arranca solo al reiniciar el servidor."
echo "  Ver registro:   journalctl -u $SERVICE -f"
echo "  Detener:        sudo systemctl stop $SERVICE"
echo "  Actualizar:     git pull && ./scripts/instalar_servidor.sh"
