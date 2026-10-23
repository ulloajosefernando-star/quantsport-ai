"""Ajustes editables en tiempo de ejecución (persistidos en la tabla app_settings)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import ValueThresholds, get_default_thresholds
from app.db.models import AppSetting

THRESHOLDS_KEY = "value_thresholds"


def get_setting(s: Session, key: str, default=None):
    row = s.get(AppSetting, key)
    return row.value if row else default


def set_setting(s: Session, key: str, value) -> None:
    row = s.get(AppSetting, key)
    if row is None:
        s.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    s.flush()


def get_thresholds(s: Session) -> ValueThresholds:
    base = get_default_thresholds().model_dump()
    override = get_setting(s, THRESHOLDS_KEY, {}) or {}
    base.update({k: v for k, v in override.items() if k in base})
    return ValueThresholds.model_construct(**base)


def update_thresholds(s: Session, changes: dict) -> ValueThresholds:
    current = get_thresholds(s).model_dump()
    unknown = set(changes) - set(current)
    if unknown:
        raise ValueError(f"Umbrales desconocidos: {', '.join(sorted(unknown))}")
    current.update(changes)
    validated = ValueThresholds.model_validate(current)  # valida tipos
    if not 0 <= validated.kelly_fraction <= 1:
        raise ValueError("kelly_fraction debe estar entre 0 y 1")
    if validated.min_odds >= validated.max_odds:
        raise ValueError("min_odds debe ser menor que max_odds")
    set_setting(s, THRESHOLDS_KEY, validated.model_dump())
    return validated
