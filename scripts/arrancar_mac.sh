#!/usr/bin/env bash
# Lo ejecuta launchd (ver instalar_mac.sh): panel y programador en http://localhost:8000.
# caffeinate -s evita que la Mac se duerma mientras esté conectada a la corriente y el proceso siga vivo.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
/usr/bin/caffeinate -s -w $$ &
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
