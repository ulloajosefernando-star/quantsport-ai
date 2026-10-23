#!/usr/bin/env bash
# Arranca backend (puerto 8000) y frontend (puerto 5173) en modo desarrollo.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"

cd "$ROOT/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements-dev.txt
fi
.venv/bin/uvicorn app.main:app --port 8000 &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT

cd "$ROOT/frontend"
[ -d node_modules ] || npm install
npm run dev
