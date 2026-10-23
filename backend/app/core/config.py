"""Configuración central (variables de entorno / .env). Nunca contiene secretos en código."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = REPO_DIR / "data"
# .env en la raíz del repositorio (y, si existe, uno en el directorio actual, que tiene prioridad)
ENV_FILES = (str(REPO_DIR / ".env"), ".env")


class ValueThresholds(BaseSettings):
    """Umbrales del motor de valor. Son parámetros editables, no constantes universales."""

    model_config = SettingsConfigDict(env_prefix="VALUE_", env_file=ENV_FILES, extra="ignore")

    min_ev: float = Field(0.03, description="EV mínimo puntual (0.03 = +3%)")
    min_edge: float = Field(0.02, description="Edge mínimo frente a la probabilidad de mercado sin margen")
    min_prob_ev_positive: float = Field(
        0.80, description="Probabilidad mínima (por incertidumbre de parámetros) de que el EV sea > 0"
    )
    target_ev: float = Field(0.02, description="EV objetivo para calcular la cuota mínima aceptable")
    min_odds: float = 1.30
    max_odds: float = 6.00
    max_odds_age_minutes: int = Field(90, description="Antigüedad máxima de una cuota para ser accionable")
    max_data_age_days: int = Field(10, description="Antigüedad máxima del último resultado usado por el modelo")
    min_eval_sample: int = Field(300, description="Predicciones fuera de muestra mínimas para validar el modelo")
    max_ece: float = Field(0.05, description="Error de calibración esperado máximo (ECE)")
    max_logloss_gap_vs_market: float = Field(
        0.0, description="Tolerancia: cuánto peor (Δ log loss) que el mercado sin margen puede ser un modelo validado"
    )
    min_team_matches: int = 8
    kelly_fraction: float = 0.25
    max_stake_fraction: float = Field(0.02, description="Exposición máxima por selección (fracción de banca)")
    max_event_exposure: float = Field(0.04, description="Exposición máxima por evento (fracción de banca)")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "cloud", "production", "test"] = "local"
    data_mode: Literal["demo", "real"] = Field(
        "demo", description="demo = datos SINTÉTICOS etiquetados; real = solo proveedores configurados"
    )
    database_url: str = f"sqlite:///{DATA_DIR / 'quantsport.db'}"
    timezone: str = "America/Mexico_City"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    admin_token: str = Field("", description="Protege endpoints de escritura. Vacío = solo modo local")
    cache_dir: Path = DATA_DIR / "cache"
    imports_dir: Path = DATA_DIR / "imports"
    demo_seed: int = 20261023

    # Proveedores (todas las claves vacías por defecto)
    the_odds_api_key: str = ""
    the_odds_api_regions: str = "eu,uk"
    the_odds_api_monthly_credits: int = 500
    football_data_org_token: str = ""
    football_data_org_per_minute: int = 10
    api_football_key: str = ""
    api_football_daily_limit: int = 100

    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""
    telegram_max_messages_per_day: int = 20

    # Presupuesto
    monthly_budget_mxn: float = 500.0
    usd_mxn_rate: float = Field(18.42, description="Tipo de cambio manual; actualízalo antes de contratar algo")
    eur_mxn_rate: float = Field(21.4, description="Tipo de cambio manual aproximado")

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_chat_ids(self) -> set[str]:
        return {c.strip() for c in self.telegram_allowed_chat_ids.split(",") if c.strip()}

    @property
    def is_demo(self) -> bool:
        return self.data_mode == "demo"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_default_thresholds() -> ValueThresholds:
    return ValueThresholds()
