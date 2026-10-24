"""Tareas de ingesta idempotentes (ejecutables desde la API o el scheduler)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.runtime_settings import get_setting, set_setting
from app.db.models import (
    BacktestRun,
    CalibrationReport,
    Competition,
    Event,
    EventStatistic,
    ModelVersion,
    OddsSnapshot,
    Prediction,
    PredictionFeature,
    ScheduledJob,
    Settlement,
    Team,
    TeamAlias,
)
from app.ingestion.store import Ingestor, IngestReport, as_utc
from app.providers import clubelo
from app.providers.csv_footballdata import parse_csv
from app.providers.demo import DemoProvider

log = get_logger(__name__)
DEMO_ANCHOR_KEY = "demo_anchor"
DEMO_MAX_AGE = timedelta(days=3)


def run_job(s: Session, name: str, run_key: str, fn: Callable[[], Any], max_attempts: int = 3) -> dict:
    """Ejecuta ``fn`` una sola vez por (name, run_key). Reintenta si la ejecución previa falló."""
    job = s.scalar(select(ScheduledJob).where(ScheduledJob.job_name == name, ScheduledJob.run_key == run_key))
    if job is not None and job.status == "ok":
        return {"status": "skipped", "reason": "ya ejecutada", "job_id": job.id}
    if job is not None and job.status == "running" and as_utc(job.started_at) > datetime.now(UTC) - timedelta(minutes=30):
        return {"status": "skipped", "reason": "en ejecución", "job_id": job.id}
    if job is not None and job.attempts >= max_attempts:
        return {"status": "skipped", "reason": f"máximo de {max_attempts} intentos alcanzado", "job_id": job.id}
    if job is None:
        job = ScheduledJob(job_name=name, run_key=run_key)
        s.add(job)
        try:
            s.commit()
        except IntegrityError:  # otra instancia la creó a la vez
            s.rollback()
            return {"status": "skipped", "reason": "duplicada"}
    else:
        job.attempts += 1
        job.status = "running"
        job.started_at = datetime.now(UTC)
        s.commit()
    try:
        result = fn()
        job.status, job.detail = "ok", str(result)[:2000]
    except Exception as exc:
        s.rollback()
        job = s.get(ScheduledJob, job.id)
        job.status, job.detail = "error", str(exc)[:2000]
        log.exception("Tarea %s/%s falló", name, run_key)
    job.finished_at = datetime.now(UTC)
    s.commit()
    return {"status": job.status, "job_id": job.id, "detail": job.detail}


def report_dict(r: IngestReport) -> dict:
    return r.__dict__


def wipe_synthetic(s: Session) -> None:
    ev_ids = select(Event.id).where(Event.is_synthetic.is_(True))
    pred_ids = select(Prediction.id).where(Prediction.event_id.in_(ev_ids))
    mv_ids = select(ModelVersion.id).where(ModelVersion.is_synthetic.is_(True))
    s.execute(delete(Settlement).where(Settlement.prediction_id.in_(pred_ids)))
    s.execute(delete(PredictionFeature).where(PredictionFeature.prediction_id.in_(pred_ids)))
    s.execute(delete(Prediction).where(Prediction.event_id.in_(ev_ids)))
    s.execute(delete(CalibrationReport).where(CalibrationReport.model_version_id.in_(mv_ids)))
    s.execute(delete(ModelVersion).where(ModelVersion.is_synthetic.is_(True)))
    s.execute(delete(BacktestRun).where(BacktestRun.is_synthetic.is_(True)))
    s.execute(delete(OddsSnapshot).where(OddsSnapshot.event_id.in_(ev_ids)))
    s.execute(delete(EventStatistic).where(EventStatistic.event_id.in_(ev_ids)))
    s.execute(delete(Event).where(Event.is_synthetic.is_(True)))
    team_ids = select(Team.id).where(Team.is_synthetic.is_(True))
    s.execute(delete(TeamAlias).where(TeamAlias.team_id.in_(team_ids)))
    s.execute(delete(Team).where(Team.is_synthetic.is_(True)))
    s.execute(delete(Competition).where(Competition.is_synthetic.is_(True)))
    s.flush()


def bootstrap_demo(s: Session, settings: Settings, force: bool = False) -> dict:
    """Carga (o regenera) la liga sintética. Se regenera si el ancla tiene > 3 días."""
    anchor_raw = get_setting(s, DEMO_ANCHOR_KEY)
    anchor = datetime.fromisoformat(anchor_raw) if anchor_raw else None
    has_data = s.scalar(select(Event.id).where(Event.is_synthetic.is_(True)).limit(1)) is not None
    now = datetime.now(UTC)
    if has_data and anchor and not force and now - anchor < DEMO_MAX_AGE:
        return {"status": "unchanged", "anchor": anchor.isoformat()}
    wipe_synthetic(s)
    provider = DemoProvider(settings.demo_seed, now=now)
    ing = Ingestor(s)
    r1 = ing.ingest(provider.fetch_results())
    r2 = ing.ingest(provider.fetch_fixtures())
    r3 = ing.ingest(provider.fetch_odds())
    set_setting(s, DEMO_ANCHOR_KEY, provider.now.isoformat())
    s.commit()
    return {"status": "regenerated", "anchor": provider.now.isoformat(),
            "results": report_dict(r1), "fixtures": report_dict(r2), "odds": report_dict(r3)}


def refresh_demo_odds(s: Session, settings: Settings) -> dict:
    anchor_raw = get_setting(s, DEMO_ANCHOR_KEY)
    if not anchor_raw:
        return bootstrap_demo(s, settings)
    provider = DemoProvider(settings.demo_seed, now=datetime.fromisoformat(anchor_raw))
    rep = Ingestor(s).ingest(provider.fetch_odds())
    s.commit()
    return report_dict(rep)


def import_csv(s: Session, content: bytes, filename: str) -> dict:
    head = (content[:200].decode("utf-8-sig", errors="ignore") if isinstance(content, bytes) else content[:200])
    if clubelo.is_clubelo_csv(head.splitlines()[0].split(",") if head else []):
        rep = clubelo.import_ratings(s, content)
        s.commit()
        return rep
    batch = parse_csv(content, filename, datetime.now(UTC))
    rep = Ingestor(s).ingest(batch)
    s.commit()
    return report_dict(rep)
