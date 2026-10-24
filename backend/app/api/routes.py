"""API REST de QuantSport AI. Documentación OpenAPI en /docs."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.bankroll.kelly import kelly_binary, suggest_stake
from app.core.config import Settings, get_settings
from app.core.runtime_settings import get_thresholds, update_thresholds
from app.db.models import (
    BacktestRun,
    Competition,
    DataSourceLog,
    Event,
    ModelVersion,
    OddsSnapshot,
    Prediction,
    Sport,
)
from app.db.session import get_db
from app.ingestion import jobs, teams
from app.ingestion.store import as_utc, seed_sports
from app.probability.ev import DEFINITIONS, ev_binary, min_odds_for_target
from app.probability.margin import compare_methods
from app.probability.odds import InvalidOddsError, decimal_to_american, decimal_to_fractional, to_decimal
from app.providers.base import ProviderError
from app.providers.normalize import COMPETITIONS
from app.providers.registry import SEASON_AWARE, all_providers, get_provider
from app.ranking.ranker import RankingFilters, analyze_upcoming, build_ranking, flatten, upcoming_events
from app.reporting import budget, predictions
from app.sports.football.service import football_service
from app.telegram.notifier import TelegramNotifier
from app.value_engine.engine import CATEGORIES

router = APIRouter(prefix="/api")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def require_admin(x_admin_token: str | None = Header(default=None),
                  settings: Settings = Depends(get_settings)) -> None:
    """Endpoints de escritura: token obligatorio salvo en modo local sin token configurado."""
    if settings.admin_token:
        if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
            raise HTTPException(401, "Token de administración inválido")
    elif settings.app_env not in ("local", "test"):
        raise HTTPException(403, "Configura ADMIN_TOKEN para habilitar escrituras fuera del modo local")


def _local(dt: datetime | None, tz: str) -> str | None:
    return as_utc(dt).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M") if dt else None


# ---------------------------------------------------------------- salud
@router.get("/health", tags=["sistema"])
def health(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "time_utc": datetime.now(UTC).isoformat(), "data_mode": settings.data_mode,
            "app_env": settings.app_env}


# ---------------------------------------------------------------- inicio
@router.get("/overview", tags=["dashboard"])
def overview(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    th = get_thresholds(db)
    analyses = analyze_upcoming(db, th, days_ahead=10)
    rows = flatten(analyses, settings.timezone)
    ranking = [r for r in sorted(rows, key=lambda r: (list(CATEGORIES).index(r["category"]),
                                                       -r.get("quality_score", 0)))
               if r["category"] in ("candidato_valor", "en_observacion", "ventaja_no_confirmada")][:10]
    counts = {k: sum(r["category"] == k for r in rows) for k in CATEGORIES}
    alerts = []
    for an in analyses:
        for issue in an["data_quality"].get("issues", []):
            alerts.append({"event": f"{an['home']} vs {an['away']}", "issue": issue})
    stale = counts.get("cuota_desactualizada", 0)
    if stale:
        alerts.append({"event": "(varios)", "issue": f"{stale} selecciones con cuotas desactualizadas."})
    last_odds = db.scalar(select(func.max(OddsSnapshot.captured_at)))
    last_event = db.scalar(select(func.max(Event.fetched_at)))
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    events = [{"event_id": a["event_id"], "competition": a["competition"], "home": a["home"], "away": a["away"],
               "kickoff_utc": a["kickoff_utc"],
               "kickoff_local": _local(datetime.fromisoformat(a["kickoff_utc"]), settings.timezone),
               "summary_category": a.get("summary_category"), "is_synthetic": a["is_synthetic"],
               "p1x2": (a.get("probabilities") or {}).get("1x2")} for a in analyses]
    return {
        "data_mode": settings.data_mode,
        "synthetic_data_present": any(a["is_synthetic"] for a in analyses),
        "events_today": [e for e in events if e["kickoff_local"] and e["kickoff_local"][:10] == str(today)],
        "upcoming": events[:20],
        "n_upcoming": len(events),
        "category_counts": counts,
        "ranking": ranking,
        "models_available": db.scalar(select(func.count(func.distinct(ModelVersion.competition_key)))) or 0,
        "last_odds_update": as_utc(last_odds).isoformat() if last_odds else None,
        "last_event_update": as_utc(last_event).isoformat() if last_event else None,
        "alerts": alerts[:30],
        "providers": [_provider_row(p) for p in all_providers(settings)],
        "sports": [{"key": sp.key, "name": sp.name, "status": sp.status, "reason": sp.status_reason}
                   for sp in db.scalars(select(Sport))],
    }


def _provider_row(p) -> dict:
    i = p.info
    return {"key": i.key, "name": i.name, "kind": i.kind.value, "sports": i.sports,
            "capabilities": [c.value for c in i.capabilities], "configured": i.configured,
            "docs_url": i.docs_url, "cost_note": i.cost_note, "coverage_note": i.coverage_note,
            "license_note": i.license_note, "quota_note": i.quota_note}


# ---------------------------------------------------------------- cartelera
@router.get("/events", tags=["cartelera"])
def list_events(days: int = Query(10, ge=1, le=30), sport: str | None = None, competition: str | None = None,
                db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    th = get_thresholds(db)
    analyses = analyze_upcoming(db, th, days, sport, competition)
    out = []
    for a in analyses:
        best = {}
        for o in a.get("odds_table", []):
            if o["market"] == "1x2":
                cur = best.get(o["selection"])
                if cur is None or o["odds"] > cur["odds"]:
                    best[o["selection"]] = o
        last_odds = max((o["captured_at"] for o in a.get("odds_table", [])), default=None)
        out.append({
            "event_id": a["event_id"], "sport": a["sport"], "competition": a["competition"],
            "competition_key": a["competition_key"], "home": a["home"], "away": a["away"],
            "kickoff_utc": a["kickoff_utc"],
            "kickoff_local": _local(datetime.fromisoformat(a["kickoff_utc"]), settings.timezone),
            "p1x2": (a.get("probabilities") or {}).get("1x2"),
            "xg": [(a.get("probabilities") or {}).get("lambda_home"), (a.get("probabilities") or {}).get("lambda_away")],
            "best_1x2": best, "bookmakers": sorted({o["bookmaker"] for o in a.get("odds_table", [])}),
            "markets": sorted({o["market"] for o in a.get("odds_table", [])}),
            "lineups_status": "no disponible (sin fuente de alineaciones configurada)",
            "last_odds_update": last_odds, "summary_category": a.get("summary_category"),
            "model_status": a["model_status"], "is_synthetic": a["is_synthetic"],
            "data_issues": a["data_quality"].get("issues", []),
        })
    comps = [{"key": c.key, "name": c.name, "is_synthetic": c.is_synthetic}
             for c in db.scalars(select(Competition).order_by(Competition.name))]
    return {"events": out, "competitions": comps}


@router.get("/events/{event_id}", tags=["cartelera"])
def event_detail(event_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    ev = db.get(Event, event_id)
    if ev is None:
        raise HTTPException(404, "Evento no encontrado")
    an = football_service.analyze_event(db, ev, get_thresholds(db))
    an["kickoff_local"] = _local(ev.kickoff_utc, settings.timezone)
    an["timezone"] = settings.timezone
    return an


# ---------------------------------------------------------------- oportunidades
@router.get("/opportunities", tags=["oportunidades"])
def opportunities(
    sport: str | None = None, competition: str | None = None, market: str | None = None,
    bookmaker: str | None = None, category: list[str] | None = Query(None), min_ev: float | None = None,
    min_edge: float | None = None, min_odds: float | None = None, max_odds: float | None = None,
    days: int = Query(10, ge=1, le=30), include_no_value: bool = False,
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
) -> dict:
    f = RankingFilters(sport, competition, market, bookmaker, category, min_ev, min_edge, min_odds, max_odds, days,
                       include_no_value)
    rows = build_ranking(db, get_thresholds(db), f, settings.timezone)
    return {"items": rows, "categories": CATEGORIES, "thresholds": get_thresholds(db).model_dump(),
            "disclaimer": "Análisis probabilístico, no garantía de resultado. El sistema no ejecuta apuestas."}


# ---------------------------------------------------------------- rendimiento
@router.get("/performance", tags=["rendimiento"])
def performance(db: Session = Depends(get_db)) -> dict:
    th = get_thresholds(db)
    comps = []
    for comp in db.scalars(select(Competition)):
        st = football_service.state(db, comp, th)
        comps.append({
            "competition": comp.name, "competition_key": comp.key, "is_synthetic": comp.is_synthetic,
            "model_status": st.status, "error": st.error,
            "gate": st.gate.checks if st.gate else [],
            "model": st.model.metadata() if st.model else None,
            "walkforward": st.walkforward,
        })
    versions = [{"id": m.id, "competition_key": m.competition_key, "name": m.name, "version": m.version,
                 "fingerprint": m.fingerprint, "trained_at": as_utc(m.trained_at).isoformat(),
                 "train_period": [m.train_start, m.train_end], "validation_period": m.validation_period,
                 "status": m.status, "status_reason": m.status_reason, "is_synthetic": m.is_synthetic,
                 "calibration": m.calibration_metrics, "economic": m.economic_metrics}
                for m in db.scalars(select(ModelVersion).order_by(ModelVersion.trained_at.desc()).limit(50))]
    return {"competitions": comps, "tracked": predictions.performance(db), "model_versions": versions,
            "backtests": db.scalar(select(func.count(BacktestRun.id))) or 0}


@router.post("/predictions/snapshot", tags=["rendimiento"], dependencies=[Depends(require_admin)])
def predictions_snapshot(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    return predictions.snapshot_ranking(db, settings.timezone)


@router.post("/predictions/settle", tags=["rendimiento"], dependencies=[Depends(require_admin)])
def predictions_settle(db: Session = Depends(get_db)) -> dict:
    return predictions.settle_pending(db)


@router.get("/predictions", tags=["rendimiento"])
def predictions_list(limit: int = Query(200, le=1000), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Prediction).order_by(Prediction.created_at.desc()).limit(limit))
    return [{"id": p.id, "created_at": as_utc(p.created_at).isoformat(), "event_id": p.event_id,
             "market": p.market, "line": p.line, "selection": p.selection, "p_model": p.p_model, "odds": p.odds,
             "bookmaker": p.bookmaker, "ev": p.ev, "edge": p.edge, "category": p.category,
             "is_synthetic": p.is_synthetic} for p in rows]


# ---------------------------------------------------------------- fuentes
@router.get("/sources", tags=["fuentes"])
def sources(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    logs = db.scalars(select(DataSourceLog).order_by(DataSourceLog.created_at.desc()).limit(50))
    coverage = []
    for comp in db.scalars(select(Competition)):
        n_fin = db.scalar(select(func.count(Event.id)).where(Event.competition_id == comp.id,
                                                             Event.status == "finished")) or 0
        n_up = db.scalar(select(func.count(Event.id)).where(Event.competition_id == comp.id,
                                                            Event.status == "scheduled")) or 0
        n_odds = db.scalar(select(func.count(OddsSnapshot.id)).join(Event).where(Event.competition_id == comp.id)) or 0
        last = db.scalar(select(func.max(Event.fetched_at)).where(Event.competition_id == comp.id))
        coverage.append({"competition": comp.name, "key": comp.key, "is_synthetic": comp.is_synthetic,
                         "finished": n_fin, "scheduled": n_up, "odds_snapshots": n_odds,
                         "last_sync": as_utc(last).isoformat() if last else None})
    return {
        "providers": [_provider_row(p) for p in all_providers(settings)],
        "usage": budget.provider_usage(db, settings),
        "coverage": coverage,
        "recent_calls": [{"time": as_utc(r.created_at).isoformat(), "provider": r.provider, "endpoint": r.endpoint,
                          "status": r.status, "http_status": r.http_status, "credits": r.credits_used,
                          "error": r.error[:200]} for r in logs],
        "csv_files": [p.name for p in sorted(settings.imports_dir.glob("*.csv"))] if settings.imports_dir.exists()
        else [],
        "catalog": [{"key": k, "name": m["name"], "country": m.get("country", ""),
                     "providers": [prov for prov, field in (("api_football", "api_football"),
                                                            ("the_odds_api", "odds_api"),
                                                            ("football_data_org", "fdorg"),
                                                            ("openfootball", "openfootball")) if m.get(field)]}
                    for k, m in COMPETITIONS.items()],
    }


@router.get("/teams/review", tags=["fuentes"])
def teams_review(competition: str = Query(..., max_length=64), db: Session = Depends(get_db)) -> dict:
    """Equipos con partidos programados pero sin historial, con sugerencias de fusión."""
    try:
        return teams.review(db, competition, get_thresholds(db).min_team_matches)
    except teams.TeamMergeError as exc:
        raise HTTPException(404, str(exc)) from exc


class TeamMergeIn(BaseModel):
    from_team_id: int = Field(gt=0)
    into_team_id: int = Field(gt=0)


@router.post("/teams/merge", tags=["fuentes"], dependencies=[Depends(require_admin)])
def teams_merge(body: TeamMergeIn, db: Session = Depends(get_db)) -> dict:
    try:
        rep = teams.merge(db, body.from_team_id, body.into_team_id)
    except teams.TeamMergeError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    football_service.invalidate()
    return rep


@router.post("/imports/csv", tags=["fuentes"], dependencies=[Depends(require_admin)])
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Archivo demasiado grande (máximo 10 MB)")
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Solo se aceptan archivos .csv")
    try:
        rep = jobs.import_csv(db, content, file.filename or "upload.csv")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    football_service.invalidate()
    return rep


@router.post("/providers/{key}/sync", tags=["fuentes"], dependencies=[Depends(require_admin)])
def provider_sync(key: str, competition: str, kind: str = Query("odds", pattern="^(odds|results|fixtures)$"),
                  season: str | None = Query(None, pattern=r"^\d{4}$"),
                  db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    p = get_provider(settings, key)
    if p is None:
        raise HTTPException(404, "Proveedor desconocido")
    fn = {"odds": p.fetch_odds, "results": p.fetch_results, "fixtures": p.fetch_fixtures}[kind]
    kwargs = {"season": season} if season and (kind == "results" or key in SEASON_AWARE) else {}
    try:
        batch = fn(competition, **kwargs)
    except ProviderError as exc:
        raise HTTPException(409, str(exc)) from exc
    from app.ingestion.store import Ingestor

    rep = Ingestor(db).ingest(batch)
    db.commit()
    football_service.invalidate()
    return jobs.report_dict(rep)


@router.post("/demo/refresh-odds", tags=["demo"], dependencies=[Depends(require_admin)])
def demo_refresh(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    if not settings.is_demo:
        raise HTTPException(409, "Solo disponible en DATA_MODE=demo")
    return jobs.refresh_demo_odds(db, settings)


@router.post("/demo/reset", tags=["demo"], dependencies=[Depends(require_admin)])
def demo_reset(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    if not settings.is_demo:
        raise HTTPException(409, "Solo disponible en DATA_MODE=demo")
    res = jobs.bootstrap_demo(db, settings, force=True)
    football_service.invalidate()
    return res


# ---------------------------------------------------------------- configuración
class ThresholdUpdate(BaseModel):
    changes: dict[str, float | int] = Field(..., description="Umbrales a modificar")


@router.get("/settings", tags=["configuración"])
def get_app_settings(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    seed_sports(db)
    return {
        "data_mode": settings.data_mode, "timezone": settings.timezone, "app_env": settings.app_env,
        "thresholds": get_thresholds(db).model_dump(),
        "sports": [{"key": sp.key, "name": sp.name, "status": sp.status, "enabled": sp.enabled,
                    "reason": sp.status_reason} for sp in db.scalars(select(Sport))],
        "competitions": [{"key": c.key, "name": c.name, "enabled": c.enabled, "is_synthetic": c.is_synthetic}
                         for c in db.scalars(select(Competition))],
        "telegram": {"configured": bool(settings.telegram_bot_token), "allowed_chats": len(settings.telegram_chat_ids),
                     "max_messages_per_day": settings.telegram_max_messages_per_day},
        "providers": [{"key": p.info.key, "name": p.info.name, "configured": p.info.configured,
                       "kind": p.info.kind.value} for p in all_providers(settings)],
        "budget": budget.cost_table(settings),
        "admin_token_required": bool(settings.admin_token),
        "schedules": "Fase 7 (scheduler) — pendiente",
    }


@router.put("/settings/thresholds", tags=["configuración"], dependencies=[Depends(require_admin)])
def put_thresholds(body: ThresholdUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        th = update_thresholds(db, body.changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    football_service.invalidate()
    return th.model_dump()


class ToggleBody(BaseModel):
    enabled: bool


@router.put("/settings/competitions/{key}", tags=["configuración"], dependencies=[Depends(require_admin)])
def toggle_competition(key: str, body: ToggleBody, db: Session = Depends(get_db)) -> dict:
    comp = db.scalar(select(Competition).where(Competition.key == key))
    if comp is None:
        raise HTTPException(404, "Competición no encontrada")
    comp.enabled = body.enabled
    db.commit()
    return {"key": key, "enabled": comp.enabled}


@router.get("/budget", tags=["configuración"])
def get_budget(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    return budget.cost_table(settings) | {"usage": budget.provider_usage(db, settings)}


# ---------------------------------------------------------------- telegram
@router.post("/telegram/send-summary", tags=["telegram"], dependencies=[Depends(require_admin)])
def telegram_summary(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    th = get_thresholds(db)
    rows = build_ranking(db, th, RankingFilters(), settings.timezone)
    n_events = len(upcoming_events(db, 10))
    return TelegramNotifier(settings).send_summary(db, rows, n_events)


@router.get("/telegram/preview", tags=["telegram"])
def telegram_preview(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    from app.telegram.notifier import format_summary

    th = get_thresholds(db)
    rows = build_ranking(db, th, RankingFilters(), settings.timezone)
    return {"messages": format_summary(rows, len(upcoming_events(db, 10)))}


# ---------------------------------------------------------------- herramientas
@router.get("/tools/odds", tags=["herramientas"])
def odds_tool(odds: list[str] = Query(..., description="Cuotas del mercado completo"), fmt: str = "decimal",
              p_model: float | None = Query(None, gt=0, lt=1), target_ev: float = 0.02, bankroll: float = 1000.0,
              kelly_fraction: float = Query(0.25, ge=0, le=1)) -> dict:
    try:
        dec = [to_decimal(o, fmt) for o in odds]
    except (InvalidOddsError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    out: dict = {"decimal": dec, "american": [decimal_to_american(d) for d in dec],
                 "fractional": [decimal_to_fractional(d) for d in dec], "implied_raw": [1 / d for d in dec],
                 "definitions": DEFINITIONS}
    if len(dec) >= 2:
        cmp = compare_methods(dec)
        out["overround"] = cmp.overround
        out["fair_by_method"] = cmp.by_method
        out["method_disagreement"] = cmp.max_disagreement
        out["note"] = cmp.note
    if p_model is not None:
        d = dec[0]
        k = kelly_binary(p_model, d)
        out["first_selection"] = {
            "ev": ev_binary(p_model, d), "fair_odds": 1 / p_model,
            "min_odds_for_target": min_odds_for_target(p_model, target_ev),
            "edge_vs_proportional": p_model - out["fair_by_method"]["proportional"][0] if len(dec) >= 2 else None,
            "kelly_full": k, "stake": suggest_stake(k, bankroll, kelly_fraction).__dict__,
        }
    return out
