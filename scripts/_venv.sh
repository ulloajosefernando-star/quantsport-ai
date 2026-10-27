# Uso: source scripts/_venv.sh; ensure_venv requirements.txt   (desde backend/)
# Busca un Python 3.12 o más nuevo y crea backend/.venv. Si el entorno quedó a medias
# (instalación fallida) o con un Python viejo, lo vuelve a crear.

find_python() {
  for c in python3.13 python3.12 python3.14 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

ensure_venv() {
  local req="$1"
  if [ -f .venv/.instalado ] && .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    return 0
  fi
  local py
  if ! py="$(find_python)"; then
    echo "Se necesita Python 3.12 o más nuevo. Tienes: $(python3 --version 2>&1)." >&2
    echo "En Mac: instálalo desde https://www.python.org/downloads/ o con 'brew install python@3.13', y vuelve a correr el script." >&2
    exit 1
  fi
  echo "Creando entorno con $("$py" --version) (solo la primera vez, tarda unos minutos)…"
  rm -rf .venv
  "$py" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/pip install -q -r "$req"
  touch .venv/.instalado
}
