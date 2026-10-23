#!/usr/bin/env bash
# Pruebas y linters. Ninguna prueba llama a APIs reales.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
.venv/bin/ruff check app tests
.venv/bin/pytest
cd "$ROOT/frontend"
npm run build
