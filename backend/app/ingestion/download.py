"""Descarga de archivos públicos permitidos (CSV de football-data.co.uk).

Solo https y solo hosts de la lista: la plataforma no descarga páginas ni hace scraping.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.providers.normalize import COMPETITIONS

ALLOWED_DOWNLOAD_HOSTS = {"www.football-data.co.uk", "football-data.co.uk"}
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
FDCOUK_NEW_FILES = {"Mexico": "MEX", "USA": "USA"}


class DownloadError(ValueError):
    pass


def download(src: str, transport: httpx.BaseTransport | None = None) -> tuple[bytes, str]:
    """Descarga ``src`` si está permitido, la guarda en imports/ y devuelve (contenido, nombre)."""
    url = urlparse(src)
    if url.scheme != "https" or url.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise DownloadError(f"Solo se descargan archivos de {', '.join(sorted(ALLOWED_DOWNLOAD_HOSTS))} por https. "
                            "Para otras fuentes, descarga el CSV y pasa la ruta local.")
    with httpx.Client(timeout=60, follow_redirects=True, transport=transport) as c:
        resp = c.get(src)
    resp.raise_for_status()
    if len(resp.content) > MAX_DOWNLOAD_BYTES:
        raise DownloadError("Archivo demasiado grande (máximo 10 MB)")
    name = url.path.rsplit("/", 1)[-1] or "descarga.csv"
    dest = get_settings().imports_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return resp.content, name


def fdcouk_url(competition_key: str, today: date | None = None) -> str | None:
    """URL del CSV de la temporada actual en football-data.co.uk, o None si la competición no está allí."""
    meta = COMPETITIONS.get(competition_key, {})
    if "fdcouk_new" in meta:
        code = FDCOUK_NEW_FILES.get(meta["fdcouk_new"][0])
        return f"https://www.football-data.co.uk/new/{code}.csv" if code else None
    if "fdcouk" in meta:
        today = today or date.today()
        start = today.year if today.month >= 7 else today.year - 1
        return f"https://www.football-data.co.uk/mmz4281/{start % 100:02d}{(start + 1) % 100:02d}/{meta['fdcouk']}.csv"
    return None
