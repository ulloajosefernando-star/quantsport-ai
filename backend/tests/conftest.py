"""Configuración de tests. Ningún test usa APIs reales: todo HTTP va por httpx.MockTransport.

Los datos de prueba son SINTÉTICOS (proveedor demo o fixtures construidos en código).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_TMP = Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / f"quantsport-tests-{os.getpid()}"
_TMP.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "APP_ENV": "test",
    "DATA_MODE": "demo",
    "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}",
    "CACHE_DIR": str(_TMP / "cache"),
    "IMPORTS_DIR": str(_TMP / "imports"),
    "LOG_JSON": "false",
    "THE_ODDS_API_KEY": "",
    "FOOTBALL_DATA_ORG_TOKEN": "",
    "TELEGRAM_BOT_TOKEN": "",
    "ADMIN_TOKEN": "",
    "API_FOOTBALL_KEY": "",
    "TELEGRAM_ALLOWED_CHAT_IDS": "",
})


@pytest.fixture(scope="session")
def db_engine():
    from app.db.migrate import upgrade_head
    from app.db.session import get_engine

    db_file = _TMP / "test.db"
    if db_file.exists():
        db_file.unlink()
    upgrade_head()
    return get_engine()


@pytest.fixture()
def session(db_engine):
    from app.db.session import get_sessionmaker

    s = get_sessionmaker()()
    yield s
    s.rollback()
    s.close()


@pytest.fixture(scope="session")
def demo_loaded(db_engine):
    from app.core.config import get_settings
    from app.db.session import session_scope
    from app.ingestion.jobs import bootstrap_demo

    with session_scope() as s:
        return bootstrap_demo(s, get_settings(), force=True)
