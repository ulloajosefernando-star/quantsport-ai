#!/usr/bin/env bash
# Copia de seguridad consistente de la base SQLite en data/backups (conserva las 14 más recientes).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${1:-$ROOT/data/quantsport.db}"
DEST="$ROOT/data/backups"
mkdir -p "$DEST"
OUT="$DEST/quantsport-$(date +%Y%m%d-%H%M%S).db"
python3 -c "import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()" "$DB" "$OUT"
ls -1t "$DEST"/quantsport-*.db | tail -n +15 | xargs -r rm --
echo "Backup: $OUT"
