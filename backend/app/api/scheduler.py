"""Estado y ejecución manual del programador automático."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import require_admin
from app.core.config import Settings, get_settings
from app.db.models import ScheduledJob
from app.db.session import get_db
from app.ingestion.store import as_utc
from app.scheduler import runner
from app.scheduler.tasks import TASK_LABELS, TASKS, monthly_plan

router = APIRouter(prefix="/api")


@router.get("/scheduler", tags=["programador"])
def scheduler_status(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    runs = db.scalars(select(ScheduledJob).where(ScheduledJob.job_name.like("programador_%")
                                                 | (ScheduledJob.job_name == "previa_liga"))
                      .order_by(ScheduledJob.started_at.desc()).limit(30))
    reason = None
    if settings.is_demo:
        reason = "Modo demo: el programador solo funciona con DATA_MODE=real."
    elif not settings.scheduler_enabled:
        reason = "Apagado. Para encenderlo pon SCHEDULER_ENABLED=true en .env y reinicia el backend."
    return {
        "enabled": settings.scheduler_enabled, "running": runner.is_running(), "reason": reason,
        "timezone": settings.timezone, "has_odds_key": bool(settings.the_odds_api_key),
        "config": {"competitions": settings.scheduler_competition_list, "odds_times": settings.scheduler_odds_time_list,
                   "daily_time": settings.scheduler_daily_time,
                   "prekickoff_minutes": settings.scheduler_prekickoff_minutes,
                   "credit_reserve": settings.scheduler_credit_reserve, "scores": settings.scheduler_scores},
        "tasks": [{"key": k, "label": v} for k, v in TASK_LABELS.items()],
        "next_runs": runner.next_runs(), "plan": monthly_plan(db, settings),
        "recent": [{"job": r.job_name.removeprefix("programador_"), "run_key": r.run_key, "status": r.status,
                    "attempts": r.attempts, "started_at": as_utc(r.started_at).isoformat(),
                    "finished_at": as_utc(r.finished_at).isoformat() if r.finished_at else None,
                    "detail": r.detail[:600]} for r in runs],
    }


@router.post("/scheduler/run/{task}", tags=["programador"], dependencies=[Depends(require_admin)])
def scheduler_run(task: str, settings: Settings = Depends(get_settings)) -> dict:
    if task not in TASKS:
        raise HTTPException(404, f"Tarea desconocida. Opciones: {', '.join(TASKS)}")
    if settings.is_demo:
        raise HTTPException(400, "El programador solo funciona con DATA_MODE=real")
    return runner.run_task(task, manual=True, settings=settings)
