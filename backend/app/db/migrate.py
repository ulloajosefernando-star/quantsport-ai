"""Aplica migraciones Alembic al arrancar (idempotente)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.core.config import BACKEND_DIR, get_settings


def upgrade_head(database_url: str | None = None) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url or get_settings().database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
