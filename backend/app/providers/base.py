"""Interfaz común de proveedores de datos.

Cada proveedor declara qué capacidades ofrece realmente. Una capacidad no declarada
lanza ``CapabilityNotSupported``: nunca se rellenan huecos con datos inventados.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ProviderKind(StrEnum):
    REAL = "real"  # API real autorizada
    DATASET = "dataset"  # dataset histórico autorizado (CSV)
    DEMO = "demo"  # datos SINTÉTICOS
    PENDING = "pending"  # adaptador pendiente de configuración / implementación


class Capability(StrEnum):
    COMPETITIONS = "competitions"
    FIXTURES = "fixtures"
    RESULTS = "results"
    HISTORICAL = "historical"
    STATISTICS = "statistics"
    ADVANCED_STATS = "advanced_stats"  # xG/xGA, EPA, etc.
    LINEUPS = "lineups"
    INJURIES = "injuries"
    ODDS = "odds"
    HISTORICAL_ODDS = "historical_odds"
    CLOSING_ODDS = "closing_odds"


class ProviderError(RuntimeError):
    pass


class CapabilityNotSupported(ProviderError):
    pass


class ProviderNotConfigured(ProviderError):
    pass


class QuotaExceeded(ProviderError):
    pass


@dataclass
class ProviderInfo:
    key: str
    name: str
    kind: ProviderKind
    sports: list[str]
    capabilities: list[Capability]
    configured: bool
    docs_url: str = ""
    cost_note: str = ""
    coverage_note: str = ""
    license_note: str = ""
    quota_note: str = ""


@dataclass
class NormalizedEvent:
    sport: str
    competition_key: str
    competition_name: str
    kickoff_utc: datetime
    home: str
    away: str
    source: str
    status: str = "scheduled"
    season: str = ""
    country: str = ""
    home_score: int | None = None
    away_score: int | None = None
    home_score_ht: int | None = None
    away_score_ht: int | None = None
    source_event_id: str = ""
    is_synthetic: bool = False
    stats: dict[str, dict[str, float]] = field(default_factory=dict)  # {"home": {...}, "away": {...}}

    def __post_init__(self) -> None:
        if self.kickoff_utc.tzinfo is None:
            raise ValueError("kickoff_utc debe tener zona horaria (UTC)")
        self.kickoff_utc = self.kickoff_utc.astimezone(UTC)


@dataclass
class NormalizedOdds:
    sport: str
    competition_key: str
    kickoff_utc: datetime
    home: str
    away: str
    bookmaker: str
    market: str  # clave de app.markets.registry (1x2, totals, btts, ah, dnb, ...)
    selection: str  # home|draw|away|over|under|yes|no|...
    odds_decimal: float
    captured_at: datetime
    source: str
    line: float | None = None
    is_closing: bool = False
    is_synthetic: bool = False
    source_event_id: str = ""
    period: str = "ft"
    participant: str | None = None
    is_live: bool = False
    original_format: str | None = None
    original_value: str | None = None


@dataclass
class ProviderBatch:
    source: str
    fetched_at: datetime
    events: list[NormalizedEvent] = field(default_factory=list)
    odds: list[NormalizedOdds] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DataProvider(ABC):
    """Contrato común. Los métodos no soportados lanzan CapabilityNotSupported."""

    info: ProviderInfo

    def supports(self, cap: Capability) -> bool:
        return cap in self.info.capabilities

    def _unsupported(self, cap: Capability) -> CapabilityNotSupported:
        return CapabilityNotSupported(f"{self.info.name} no ofrece '{cap}' (o no en el plan configurado)")

    def fetch_competitions(self) -> list[dict]:
        raise self._unsupported(Capability.COMPETITIONS)

    def fetch_results(self, competition_key: str, season: str | None = None) -> ProviderBatch:
        raise self._unsupported(Capability.RESULTS)

    def fetch_fixtures(self, competition_key: str, days_ahead: int = 7) -> ProviderBatch:
        raise self._unsupported(Capability.FIXTURES)

    def fetch_odds(self, competition_key: str) -> ProviderBatch:
        raise self._unsupported(Capability.ODDS)

    def fetch_lineups(self, event_ref: str) -> dict:
        raise self._unsupported(Capability.LINEUPS)

    def fetch_injuries(self, competition_key: str) -> dict:
        raise self._unsupported(Capability.INJURIES)


def utcnow() -> datetime:
    return datetime.now(UTC)
