"""Tareas del programador automático (Fase 7).

Tres tareas, todas idempotentes (``jobs.run_job`` con una clave por día o por horario):

    diaria   calendario (gratis), resultados del CSV de football-data.co.uk (gratis) y de The Odds API
             /scores (2 créditos por liga), liquidación de pronósticos con CLV y registro del ranking.
    cuotas   cuotas de The Odds API (mercados × regiones créditos por liga) y registro del ranking.
    previa   una captura extra unos minutos antes del inicio de cada horario de partidos; esa última
             cuota antes del inicio es la referencia de cierre para el CLV.

Nunca se pasa del presupuesto: antes de cada llamada de pago se comprueba que los créditos usados este mes
más el coste no superen el límite menos la reserva. Solo funciona en modo real: en modo demo no toca nada.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Competition, Event
from app.ingestion import jobs
from app.ingestion.download import download, fdcouk_url
from app.ingestion.jobs import report_dict as report_summary
from app.ingestion.store import Ingestor, as_utc
from app.providers.http import credits_used
from app.providers.normalize import COMPETITIONS
from app.providers.the_odds_api import SOURCE as ODDS_API
from app.providers.the_odds_api import TheOddsApiProvider
from app.reporting import predictions

log = get_logger(__name__)
ODDS_MARKETS = ("h2h", "totals")
PREKICKOFF_TICK_MINUTES = 15


class NotRealModeError(RuntimeError):
    pass


def odds_call_cost(settings: Settings) -> int:
    regions = [r for r in settings.the_odds_api_regions.split(",") if r.strip()]
    return len(ODDS_MARKETS) * max(len(regions), 1)


def budget_left(settings: Settings) -> float:
    """Créditos de The Odds API que el programador todavía puede gastar este mes."""
    return settings.the_odds_api_monthly_credits - settings.scheduler_credit_reserve - credits_used(ODDS_API, "month")


def _can_spend(settings: Settings, cost: float) -> bool:
    return budget_left(settings) >= cost


def _odds_competitions(settings: Settings) -> list[str]:
    return [c for c in settings.scheduler_competition_list if COMPETITIONS.get(c, {}).get("odds_api")]


def _check_mode(settings: Settings) -> None:
    if settings.is_demo:
        raise NotRealModeError("El programador solo funciona con DATA_MODE=real (en demo los datos son sintéticos).")


def _step(s: Session, out: dict, name: str, fn: Callable[[], dict | str]) -> None:
    """Ejecuta un paso y confirma sus cambios; un error en una liga o fuente no detiene el resto.

    Confirmar tras cada paso también libera la base antes de la siguiente llamada a un proveedor (con SQLite,
    el registro de llamadas escribe en otra sesión y no puede esperar a una transacción abierta).
    """
    try:
        out[name] = fn()
        s.commit()
    except Exception as exc:  # se registra y se sigue
        s.rollback()
        log.warning("Paso %s falló: %s", name, exc)
        out[name] = f"error: {exc}"


def sync_odds(s: Session, settings: Settings, comp: str, provider: TheOddsApiProvider | None = None) -> dict | str:
    if not settings.the_odds_api_key:
        return "omitido: falta THE_ODDS_API_KEY"
    cost = odds_call_cost(settings)
    if not _can_spend(settings, cost):
        return f"omitido por presupuesto: quedan {budget_left(settings):.0f} créditos (coste {cost}, reserva incluida)"
    p = provider or TheOddsApiProvider(settings)
    return report_summary(Ingestor(s).ingest(p.fetch_odds(comp, ODDS_MARKETS)))


def task_daily(s: Session, settings: Settings, now: datetime | None = None,
               provider: TheOddsApiProvider | None = None) -> dict:
    _check_mode(settings)
    out: dict = {}
    p = provider or TheOddsApiProvider(settings)
    for comp in settings.scheduler_competition_list:
        if comp not in COMPETITIONS:
            out[comp] = "competición desconocida"
            continue
        url = fdcouk_url(comp, (now or datetime.now(UTC)).date())
        if url:
            _step(s, out, f"{comp}:csv", lambda url=url: jobs.import_csv(s, *download(url)))
        if settings.the_odds_api_key and COMPETITIONS[comp].get("odds_api"):
            _step(s, out, f"{comp}:calendario", lambda comp=comp: report_summary(Ingestor(s).ingest(p.fetch_fixtures(comp))))
            if settings.scheduler_scores:
                if _can_spend(settings, 2):
                    _step(s, out, f"{comp}:resultados",
                          lambda comp=comp: report_summary(Ingestor(s).ingest(p.fetch_scores(comp, 3))))
                else:
                    out[f"{comp}:resultados"] = "omitido por presupuesto"
    _step(s, out, "liquidacion", lambda: predictions.settle_pending(s))
    _step(s, out, "pronosticos", lambda: predictions.snapshot_ranking(s, settings.timezone))
    return out


def task_odds(s: Session, settings: Settings, now: datetime | None = None,
              provider: TheOddsApiProvider | None = None) -> dict:
    _check_mode(settings)
    out: dict = {}
    p = provider or TheOddsApiProvider(settings)
    for comp in _odds_competitions(settings):
        _step(s, out, f"{comp}:cuotas", lambda comp=comp: sync_odds(s, settings, comp, p))
    _step(s, out, "pronosticos", lambda: predictions.snapshot_ranking(s, settings.timezone))
    return out


def upcoming_kickoffs(s: Session, comp: str, start: datetime, end: datetime) -> list[datetime]:
    q = (select(Event.kickoff_utc).join(Competition, Event.competition_id == Competition.id)
         .where(Competition.key == comp, Event.status == "scheduled", Event.is_synthetic.is_(False),
                Event.kickoff_utc > start, Event.kickoff_utc <= end))
    return sorted({as_utc(k) for k in s.scalars(q)})


def task_prekickoff(s: Session, settings: Settings, now: datetime | None = None,
                    provider: TheOddsApiProvider | None = None) -> dict:
    """Captura cuotas una vez por liga y horario de inicio, ``scheduler_prekickoff_minutes`` antes."""
    _check_mode(settings)
    now = now or datetime.now(UTC)
    lead = timedelta(minutes=settings.scheduler_prekickoff_minutes)
    out: dict = {}
    if not lead or not settings.the_odds_api_key:
        return {"estado": "desactivada o sin clave"}
    p = provider or TheOddsApiProvider(settings)
    for comp in _odds_competitions(settings):
        # partidos que empiezan entre ahora+lead-tick y ahora+lead (cada tick cubre su ventana)
        kicks = upcoming_kickoffs(s, comp, now + lead - timedelta(minutes=PREKICKOFF_TICK_MINUTES), now + lead)
        if not kicks:
            continue
        key = f"{comp}:{kicks[0]:%Y-%m-%dT%H:%M}"
        out[key] = jobs.run_job(s, "previa_liga", key, lambda comp=comp: sync_odds(s, settings, comp, p))
    if out:
        _step(s, out, "pronosticos", lambda: predictions.snapshot_ranking(s, settings.timezone))
    return out or {"estado": "sin partidos en la ventana"}


TASKS: dict[str, Callable[..., dict]] = {"diaria": task_daily, "cuotas": task_odds, "previa": task_prekickoff}
TASK_LABELS = {
    "diaria": "Resultados, calendario, liquidación y registro de pronósticos",
    "cuotas": "Cuotas de The Odds API y registro de pronósticos",
    "previa": "Captura antes del inicio (referencia de cierre para el CLV)",
}


def monthly_plan(s: Session, settings: Settings, now: datetime | None = None) -> dict:
    """Créditos estimados al mes con la configuración actual."""
    now = now or datetime.now(UTC)
    comps = _odds_competitions(settings) if settings.the_odds_api_key else []
    cost = odds_call_cost(settings)
    odds = len(comps) * len(settings.scheduler_odds_time_list) * 30 * cost
    scores = len(comps) * 30 * 2 if settings.scheduler_scores else 0
    slots = 0
    if settings.scheduler_prekickoff_minutes:
        for c in comps:
            week = upcoming_kickoffs(s, c, now, now + timedelta(days=7))
            slots += len({k.replace(minute=0) for k in week}) or 12  # sin calendario: ~12 horarios por semana
    prekick = round(slots * 30 / 7) * cost
    total = odds + scores + prekick
    limit = settings.the_odds_api_monthly_credits - settings.scheduler_credit_reserve
    return {"competitions": comps, "odds_call_cost": cost, "odds": odds, "scores": scores, "prekickoff": prekick,
            "total": total, "available": limit, "monthly_limit": settings.the_odds_api_monthly_credits,
            "reserve": settings.scheduler_credit_reserve, "used_this_month": credits_used(ODDS_API, "month"),
            "fits": total <= limit,
            "note": ("Si se acaba el presupuesto del mes, el programador se salta las llamadas de pago y sigue con "
                     "lo gratuito (CSV, calendario, liquidación).")}
