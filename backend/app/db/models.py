"""Modelo de datos.

Principios:
* Todos los timestamps se guardan en UTC.
* Cada registro conserva su procedencia (``source``) y si es sintético (``is_synthetic``).
* Los snapshots de cuotas son inmutables: nunca se sobrescriben, se insertan nuevos.
* Cada predicción guarda lo que se sabía al emitirla (cuota, timestamp, versión, explicación).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Sport(Base):
    __tablename__ = "sports"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)  # football, nba, nfl, mlb, tennis
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="disabled")  # disabled|partial|active
    status_reason: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Competition(Base):
    __tablename__ = "competitions"
    __table_args__ = (UniqueConstraint("sport_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"))
    key: Mapped[str] = mapped_column(String(64))  # p. ej. "mex_liga_mx", "eng_premier_league"
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(64), default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sport: Mapped[Sport] = relationship()


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("competition_id", "label"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    label: Mapped[str] = mapped_column(String(32))  # "2025-26", "Apertura 2026"


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("sport_id", "canonical_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"))
    canonical_key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("team_id", "source", "alias"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    source: Mapped[str] = mapped_column(String(64))
    alias: Mapped[str] = mapped_column(String(128))


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(64), default="")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("competition_id", "kickoff_utc", "home_team_id", "away_team_id", name="uq_event_natural"),
        Index("ix_events_kickoff", "kickoff_utc"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"))
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    season_label: Mapped[str] = mapped_column(String(32), default="")
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    status: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled|finished|postponed
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_score_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    source_event_id: Mapped[str] = mapped_column(String(128), default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    competition: Mapped[Competition] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class EventStatistic(Base):
    __tablename__ = "event_statistics"
    __table_args__ = (UniqueConstraint("event_id", "team_side", "stat", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    team_side: Mapped[str] = mapped_column(String(8))  # home|away
    stat: Mapped[str] = mapped_column(String(32))  # shots, shots_on_target, corners, xg...
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64))


class Market(Base):
    __tablename__ = "markets"
    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"))
    key: Mapped[str] = mapped_column(String(32))  # 1x2, totals, btts, ah, dnb, dc
    name: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    disabled_reason: Mapped[str] = mapped_column(Text, default="")


class OddsSnapshot(Base):
    """Inmutable. Una fila por (evento, operador, mercado, línea, selección, momento de captura)."""

    __tablename__ = "odds_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "bookmaker", "market", "line", "selection", "captured_at", "source", name="uq_odds_snapshot"
        ),
        Index("ix_odds_event_market", "event_id", "market"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    bookmaker: Mapped[str] = mapped_column(String(64))
    market: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    selection: Mapped[str] = mapped_column(String(32))
    odds_decimal: Mapped[float] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version", "fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sport_key: Mapped[str] = mapped_column(String(32))
    competition_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(32))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    train_start: Mapped[str] = mapped_column(String(40), default="")
    train_end: Mapped[str] = mapped_column(String(40), default="")
    validation_period: Mapped[str] = mapped_column(String(100), default="")
    features: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    calibration_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    economic_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="experimental")  # experimental|production|blocked
    status_reason: Mapped[str] = mapped_column(Text, default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "model_version_id", "market", "line", "selection", "bookmaker", "odds_captured_at",
            name="uq_prediction_snapshot",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    market: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    selection: Mapped[str] = mapped_column(String(32))
    p_model: Mapped[float] = mapped_column(Float)
    p_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_market_fair: Mapped[float | None] = mapped_column(Float, nullable=True)
    odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    bookmaker: Mapped[str] = mapped_column(String(64), default="")
    odds_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fair_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    category: Mapped[str] = mapped_column(String(32))
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    closing_odds: Mapped[float | None] = mapped_column(Float, nullable=True)


class PredictionFeature(Base):
    __tablename__ = "prediction_features"
    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"))
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(128))


class CalibrationReport(Base):
    __tablename__ = "calibration_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    market: Mapped[str] = mapped_column(String(32))
    n: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    reliability: Mapped[list] = mapped_column(JSON, default=list)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sport_key: Mapped[str] = mapped_column(String(32))
    competition_key: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    odds_kind: Mapped[str] = mapped_column(String(16), default="none")  # real|hypothetical|none
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Settlement(Base):
    __tablename__ = "settlements"
    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), unique=True)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state: Mapped[str] = mapped_column(String(16))  # gana|medio_gana|push|medio_pierde|pierde|anulada
    net_return: Mapped[float] = mapped_column(Float)


class BankrollTransaction(Base):
    __tablename__ = "bankroll_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(16))  # initial|deposit|withdrawal|adjustment
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")


class DataSourceLog(Base):
    __tablename__ = "data_source_logs"
    __table_args__ = (Index("ix_dslog_provider_time", "provider", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    provider: Mapped[str] = mapped_column(String(64))
    endpoint: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16))  # ok|error|cache|blocked
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_used: Mapped[float] = mapped_column(Float, default=0.0)
    credits_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (UniqueConstraint("job_name", "run_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64))
    run_key: Mapped[str] = mapped_column(String(128))  # clave idempotente, p. ej. fecha
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    detail: Mapped[str] = mapped_column(Text, default="")


class TelegramSubscription(Base):
    __tablename__ = "telegram_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sports: Mapped[list] = mapped_column(JSON, default=lambda: ["football"])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("channel", "chat_id", "dedupe_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    channel: Mapped[str] = mapped_column(String(16), default="telegram")
    chat_id: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # sent|error|skipped
    body: Mapped[str] = mapped_column(Text)
    error: Mapped[str] = mapped_column(Text, default="")


class AppSetting(Base):
    """Ajustes editables desde el dashboard (sobrescriben los valores por defecto de .env)."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TeamRating(Base):
    """Rating externo de un equipo (p. ej. Club Elo) vigente en un rango de fechas. Solo referencia."""

    __tablename__ = "team_ratings"
    __table_args__ = (UniqueConstraint("team_id", "source", "valid_from"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    rating: Mapped[float] = mapped_column(Float)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
