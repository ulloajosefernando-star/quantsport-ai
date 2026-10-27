"""Endpoints del módulo universal de cuotas y mercados (solo lectura y cálculo; nunca apuesta)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import require_admin
from app.bankroll.kelly import kelly_binary, kelly_states, suggest_stake
from app.core.runtime_settings import get_thresholds
from app.db.models import Competition, Event, OddsSnapshot, Sport
from app.db.session import get_db
from app.ingestion.store import as_utc
from app.markets import registry
from app.markets.evaluate import evaluate_book
from app.markets.models import coverage, no_model_reason
from app.markets.quotes import event_ref, load_books
from app.probability.ev import DEFINITIONS, ev_binary, min_odds_for_target, min_odds_for_target_states
from app.probability.margin import METHODS
from app.probability.odds import FORMATS, InvalidOddsError, convert_all, detect_format, to_decimal
from app.probability.settlement import aggregate
from app.ranking.ranker import upcoming_events
from app.sports.football.data import MANUAL_SOURCE
from app.sports.football.service import football_service

router = APIRouter(prefix="/api")

DISCLAIMER = "Análisis probabilístico, no garantía de resultado. La plataforma no se conecta a casas ni apuesta."
FORMAT_PATTERN = "^(auto|decimal|american|fractional|percent)$"


@router.get("/markets/registry", tags=["mercados"])
def market_registry(sport: str | None = None) -> dict:
    cov = coverage()
    sports = [sport] if sport else registry.sports()
    if sport and sport not in registry.SPORT_LABELS:
        raise HTTPException(404, "Deporte no registrado")
    return {
        "sports": [{"key": sp, "label": registry.SPORT_LABELS[sp],
                    "markets": [m.to_dict() | {"model_reason": cov[sp][m.key], "has_model": cov[sp][m.key] is None}
                                for m in registry.for_sport(sp)]} for sp in sports],
        "formats": list(FORMATS), "margin_methods": list(METHODS), "definitions": DEFINITIONS,
        "note": "Los mercados registrados son categorías soportadas, no una garantía de que una fuente los ofrezca.",
    }


@router.get("/markets/events", tags=["mercados"])
def market_events(sport: str | None = None, competition: str | None = None, days: int = Query(10, ge=1, le=30),
                  db: Session = Depends(get_db)) -> dict:
    evs = upcoming_events(db, days, sport, competition)
    comps = db.execute(select(Competition.key, Competition.name, Sport.key).join(Sport, Competition.sport_id == Sport.id)
                       .where(Competition.enabled.is_(True)).order_by(Competition.name)).all()
    return {"events": [{"id": e.id, "label": f"{e.home_team.name} vs {e.away_team.name}",
                        "competition_key": e.competition.key, "kickoff_utc": as_utc(e.kickoff_utc).isoformat(),
                        "is_synthetic": e.is_synthetic} for e in evs],
            "competitions": [{"key": k, "name": n, "sport": sp} for k, n, sp in comps
                             if not sport or sp == sport]}


def _event_model(db: Session, ev: Event, th):
    sport = event_ref(ev).sport
    if sport == "nba":
        from app.sports.nba.service import nba_service

        em = nba_service.market_model(db, ev, th)
        return em.model, em.reason, em.data_issues, em.calibration_score, em.sample_score
    if sport != "football":
        return None, no_model_reason(sport), [], 0.0, None
    em = football_service.market_model(db, ev, th)
    return em.model, em.reason, em.data_issues, em.calibration_score, em.sample_score


def _in_range(x: float | None, lo: float | None, hi: float | None) -> bool:
    if lo is None and hi is None:
        return True
    if x is None:
        return False
    return (lo is None or x >= lo) and (hi is None or x <= hi)


@router.get("/markets/board", tags=["mercados"])
def market_board(
    sport: str | None = "football", competition: str | None = None, event_id: int | None = None,
    market: str | None = None, bookmaker: str | None = None,
    margin_method: str = Query("proportional", pattern="^(proportional|power|shin)$"),
    min_odds: float | None = Query(None, gt=1), max_odds: float | None = Query(None, gt=1),
    min_prob: float | None = Query(None, ge=0, le=1), max_prob: float | None = Query(None, ge=0, le=1),
    min_ev: float | None = None, min_edge: float | None = None,
    days: int = Query(7, ge=1, le=30), max_events: int = Query(40, ge=1, le=200), db: Session = Depends(get_db),
) -> dict:
    th = get_thresholds(db)
    if event_id is not None:
        ev = db.get(Event, event_id)
        if ev is None:
            raise HTTPException(404, "Evento no encontrado")
        events = [ev]
    else:
        events = upcoming_events(db, days, sport, competition)[:max_events]
    books, warnings = load_books(db, events, market, bookmaker)
    models: dict[int, tuple] = {}
    out = []
    now = datetime.now(UTC)
    for b in books:
        if b.event.id not in models:
            models[b.event.id] = _event_model(db, db.get(Event, b.event.id), th)
        model, reason, issues, calib, sample = models[b.event.id]
        res = evaluate_book(b, model, reason, th, margin_method, now, issues, calib, sample)
        res["selections"] = [r for r in res["selections"]
                             if _in_range(r["quote"]["odds_decimal"], min_odds, max_odds)
                             and _in_range(r["implied_probability"], min_prob, max_prob)
                             and _in_range(r["ev"], min_ev, None) and _in_range(r["edge"], min_edge, None)]
        if res["selections"]:
            out.append(res)
    return {"markets": out, "warnings": warnings, "events_considered": len(events),
            "margin_method": margin_method, "margin_methods": list(METHODS), "disclaimer": DISCLAIMER}


@router.get("/tools/convert", tags=["herramientas"])
def convert_odds(value: str = Query(..., min_length=1, max_length=16), fmt: str = Query("auto", pattern=FORMAT_PATTERN),
                 p: float | None = Query(None, gt=0, lt=1, description="Tu probabilidad de ganar (0-1)"),
                 p_push: float = Query(0.0, ge=0, lt=1, description="Probabilidad de devolución (0-1)"),
                 target_ev: float = Query(0.02, gt=-1, lt=1), bankroll: float = Query(1000.0, gt=0),
                 kelly_fraction: float = Query(0.25, ge=0, le=1)) -> dict:
    try:
        detected = detect_format(value) if fmt == "auto" else fmt
        d = to_decimal(value, fmt)
    except (InvalidOddsError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    out: dict = convert_all(d) | {"input": value, "format_detected": detected, "definitions": DEFINITIONS}
    if p is not None:
        if p + p_push > 1 + 1e-12:
            raise HTTPException(400, "La probabilidad de ganar más la de devolución no puede superar 1")
        if p_push == 0:
            ev, fair, mn, k = ev_binary(p, d), 1 / p, min_odds_for_target(p, target_ev), kelly_binary(p, d)
        else:
            dist = aggregate([(p, "gana", d - 1), (p_push, "push", 0.0), (1 - p - p_push, "pierde", -1.0)])
            ev, k = dist.expected_value, kelly_states(dist)
            fair, mn = min_odds_for_target_states(p, p_push, 0.0), min_odds_for_target_states(p, p_push, target_ev)
        out["evaluation"] = {"p": p, "p_push": p_push, "ev": ev, "fair_odds": fair, "min_odds_for_target": mn,
                             "target_ev": target_ev, "edge_vs_implied": p - 1 / d, "kelly_full": k,
                             "stake": suggest_stake(k, bankroll, kelly_fraction).__dict__}
    return out


class ManualOddsIn(BaseModel):
    """Cuota vista a mano en cualquier casa o fuente. Solo se guarda y se evalúa; no se apuesta nada."""

    bookmaker: str = Field("Manual", min_length=1, max_length=40)
    market: str = Field(..., max_length=40, pattern=r"^[a-z0-9_]+$")
    selection: str = Field(..., min_length=1, max_length=20)
    line: float | None = Field(None, ge=-1000, le=1000)
    period: str | None = Field(None, max_length=16)
    participant: str | None = Field(None, max_length=80)
    odds: str = Field(..., min_length=1, max_length=16)
    format: str = Field("auto", pattern=FORMAT_PATTERN)


def _clean(text: str) -> str:
    return " ".join("".join(c for c in text if c.isalnum() or c in " .-_'").split())


@router.post("/events/{event_id}/manual-odds", tags=["mercados"], dependencies=[Depends(require_admin)])
def manual_odds(event_id: int, body: ManualOddsIn, db: Session = Depends(get_db)) -> dict:
    ev = db.get(Event, event_id)
    if ev is None:
        raise HTTPException(404, "Evento no encontrado")
    if ev.status != "scheduled" or as_utc(ev.kickoff_utc) <= datetime.now(UTC):
        raise HTTPException(400, "Solo se capturan cuotas de partidos que aún no empiezan")
    ref = event_ref(ev)
    participant = _clean(body.participant) if body.participant else None
    try:
        v = registry.validate(ref.sport, body.market, body.selection, body.line, body.period, participant)
    except registry.MarketValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    if v.market.live_only:
        raise HTTPException(400, "Mercado en vivo: la captura manual es solo para cuotas prepartido")
    bookmaker = _clean(body.bookmaker)
    if not bookmaker:
        raise HTTPException(400, "Nombre de casa o fuente no válido")
    try:
        fmt = detect_format(body.odds) if body.format == "auto" else body.format
        dec = to_decimal(body.odds.strip(), fmt)
    except (InvalidOddsError, ValueError) as exc:
        raise HTTPException(400, f"Cuota no válida: {exc}") from exc
    if not 1.01 <= dec <= 1000:
        raise HTTPException(400, "La cuota decimal debe estar entre 1.01 y 1000")
    now = datetime.now(UTC)
    db.add(OddsSnapshot(event_id=ev.id, bookmaker=bookmaker, market=v.market.key, line=v.line, period=v.period,
                        participant=v.participant, selection=v.selection, odds_decimal=dec, captured_at=now,
                        is_closing=False, is_live=False, source=MANUAL_SOURCE, is_synthetic=ev.is_synthetic,
                        original_format=fmt, original_value=body.odds.strip()[:24]))
    db.commit()
    th = get_thresholds(db)
    books, _ = load_books(db, [ev], v.market.key)
    mine = next(b for b in books if b.bookmaker == bookmaker and b.market.period == v.period
                and b.market.participant == v.participant and b.market.line == v.line)
    model, reason, issues, calib, sample = _event_model(db, ev, th)
    res = evaluate_book(mine, model, reason, th, "proportional", now, issues, calib, sample)
    row = next(r for r in res["selections"] if r["selection"]["selection"] == v.selection)
    others = [q.odds_decimal for b in books if b.bookmaker != bookmaker and b.market.period == v.period
              and b.market.participant == v.participant and b.market.line == v.line
              for s, q in b.entries if s.selection == v.selection]
    return {"bookmaker": bookmaker, "odds_decimal": dec, "conversions": convert_all(dec), "format": fmt,
            "evaluation": row, "margin": res["margin"], "model_note": res["model_note"],
            "best_other_odds": max(others) if others else None, "disclaimer": DISCLAIMER}
