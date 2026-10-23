"""Ranking transversal de oportunidades.

No se ordena solo por EV: primero por categoría (que ya exige validez de datos, frescura,
calibración, EV e incertidumbre) y dentro de cada categoría por una puntuación de calidad.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import ValueThresholds
from app.core.logging import get_logger
from app.db.models import Competition, Event, Sport
from app.sports.football.service import football_service
from app.value_engine.engine import CATEGORY_ORDER

log = get_logger(__name__)


@dataclass
class RankingFilters:
    sport: str | None = None
    competition: str | None = None
    market: str | None = None
    bookmaker: str | None = None
    categories: list[str] | None = None
    min_ev: float | None = None
    min_edge: float | None = None
    min_odds: float | None = None
    max_odds: float | None = None
    days_ahead: int = 10
    include_no_value: bool = False


def upcoming_events(s: Session, days_ahead: int, sport: str | None = None,
                    competition: str | None = None) -> list[Event]:
    now = datetime.now(UTC)
    q = (select(Event).join(Competition, Event.competition_id == Competition.id)
         .join(Sport, Event.sport_id == Sport.id)
         .where(Event.status == "scheduled", Event.kickoff_utc >= now - timedelta(hours=2),
                Event.kickoff_utc <= now + timedelta(days=days_ahead), Competition.enabled.is_(True),
                Sport.enabled.is_(True))
         .order_by(Event.kickoff_utc))
    if sport:
        q = q.where(Sport.key == sport)
    if competition:
        q = q.where(Competition.key == competition)
    return list(s.scalars(q))


def analyze_upcoming(s: Session, th: ValueThresholds, days_ahead: int = 10, sport: str | None = None,
                     competition: str | None = None) -> list[dict]:
    out = []
    for ev in upcoming_events(s, days_ahead, sport, competition):
        if ev.sport_id and ev.competition and ev.competition.sport.key != "football":
            continue  # otros deportes: deshabilitados en Fase 1
        try:
            out.append(football_service.analyze_event(s, ev, th))
        except Exception:
            log.exception("Fallo al analizar evento %s", ev.id)
    return out


def flatten(analyses: list[dict], tz: str) -> list[dict]:
    zone = ZoneInfo(tz)
    rows = []
    for an in analyses:
        ko = datetime.fromisoformat(an["kickoff_utc"])
        for a in an["assessments"]:
            if not a["n_offers"]:
                continue
            rows.append(a | {
                "event_id": an["event_id"], "sport": an["sport"], "competition": an["competition"],
                "competition_key": an["competition_key"], "event": f"{an['home']} vs {an['away']}",
                "kickoff_utc": an["kickoff_utc"], "kickoff_local": ko.astimezone(zone).strftime("%Y-%m-%d %H:%M"),
                "model_version": (an["model"] or {}).get("version"),
                "model_fingerprint": (an["model"] or {}).get("fingerprint"),
                "sample_size": (an["model"] or {}).get("n_matches"),
                "is_synthetic": a["is_synthetic"] or an["is_synthetic"],
            })
    return rows


def rank(rows: list[dict], f: RankingFilters) -> list[dict]:
    def keep(r: dict) -> bool:
        if not f.include_no_value and r["category"] in ("sin_valor",):
            return False
        if f.categories and r["category"] not in f.categories:
            return False
        if f.market and r["market"] != f.market:
            return False
        if f.bookmaker and r.get("bookmaker") != f.bookmaker:
            return False
        if f.min_ev is not None and (r.get("ev") is None or r["ev"] < f.min_ev):
            return False
        if f.min_edge is not None and (r.get("edge") is None or r["edge"] < f.min_edge):
            return False
        if f.min_odds is not None and (r.get("odds") or 0) < f.min_odds:
            return False
        return not (f.max_odds is not None and (r.get("odds") or 0) > f.max_odds)

    kept = [r for r in rows if keep(r)]
    kept.sort(key=lambda r: (CATEGORY_ORDER.index(r["category"]), -r.get("quality_score", 0.0)))
    for i, r in enumerate(kept, 1):
        r["rank"] = i
    return kept


def build_ranking(s: Session, th: ValueThresholds, f: RankingFilters, tz: str) -> list[dict]:
    analyses = analyze_upcoming(s, th, f.days_ahead, f.sport, f.competition)
    return rank(flatten(analyses, tz), f)
