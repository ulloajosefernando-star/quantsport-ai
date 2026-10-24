"""Arranque del programador (APScheduler) dentro del backend o como proceso aparte.

Una sola instancia debe tener el programador encendido. Aunque se encendieran dos, cada tarea usa una
clave única por día u horario en ``scheduled_jobs``, así que no se ejecuta dos veces.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.ingestion.jobs import run_job
from app.scheduler.tasks import PREKICKOFF_TICK_MINUTES, TASKS

log = get_logger(__name__)
_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def run_key(name: str, settings: Settings, now: datetime | None = None, manual: bool = False) -> str:
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(settings.timezone))
    if manual:
        return f"manual:{local:%Y-%m-%dT%H:%M:%S}"
    if name == "diaria":
        return f"{local:%Y-%m-%d}"
    if name == "cuotas":
        return f"{local:%Y-%m-%dT%H}"  # una por franja horaria
    return f"{local:%Y-%m-%dT%H:%M}"


def run_task(name: str, manual: bool = False, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    if name not in TASKS:
        raise KeyError(name)
    with session_scope() as s:
        return run_job(s, f"programador_{name}", run_key(name, settings, manual=manual),
                       lambda: TASKS[name](s, settings), max_attempts=3)


def _hm(text: str) -> tuple[int, int]:
    h, m = text.split(":")
    hour, minute = int(h), int(m)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"Hora inválida: {text}")
    return hour, minute


def build(settings: Settings) -> BackgroundScheduler:
    tz = ZoneInfo(settings.timezone)
    sched = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1,
                                                           "misfire_grace_time": 3600})
    h, m = _hm(settings.scheduler_daily_time)
    sched.add_job(run_task, CronTrigger(hour=h, minute=m, timezone=tz), args=["diaria"], id="diaria",
                  name="diaria")
    for t in settings.scheduler_odds_time_list:
        h, m = _hm(t)
        sched.add_job(run_task, CronTrigger(hour=h, minute=m, timezone=tz), args=["cuotas"], id=f"cuotas_{t}",
                      name="cuotas")
    if settings.scheduler_prekickoff_minutes:
        sched.add_job(run_task, IntervalTrigger(minutes=PREKICKOFF_TICK_MINUTES, timezone=tz), args=["previa"],
                      id="previa", name="previa")
    return sched


def start(settings: Settings | None = None) -> bool:
    """Enciende el programador si está activado y en modo real. Devuelve si quedó encendido."""
    global _scheduler
    settings = settings or get_settings()
    if not settings.scheduler_enabled:
        return False
    if settings.is_demo:
        log.warning("SCHEDULER_ENABLED=true ignorado: el programador solo funciona con DATA_MODE=real")
        return False
    with _lock:
        if _scheduler is not None:
            return True
        _scheduler = build(settings)
        _scheduler.start()
    # Si el equipo estuvo apagado a la hora programada, la tarea diaria se pone al día (idempotente por fecha)
    threading.Thread(target=_catch_up, daemon=True).start()
    log.info("Programador encendido: %s", [j.id for j in _scheduler.get_jobs()])
    return True


def _catch_up() -> None:
    try:
        run_task("diaria")
    except Exception:
        log.exception("Puesta al día de la tarea diaria falló")


def stop() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None


def next_runs() -> list[dict]:
    if _scheduler is None:
        return []
    return [{"id": j.id, "task": j.name, "next_run": j.next_run_time.astimezone(UTC).isoformat()
             if j.next_run_time else None} for j in _scheduler.get_jobs()]


def is_running() -> bool:
    return _scheduler is not None
