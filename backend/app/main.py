"""Punto de entrada FastAPI."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.markets import router as markets_router
from app.api.routes import router
from app.api.scheduler import router as scheduler_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.migrate import upgrade_head
from app.db.session import session_scope
from app.ingestion import jobs
from app.ingestion.store import seed_sports
from app.scheduler import runner as scheduler

log = get_logger("quantsport")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.imports_dir.mkdir(parents=True, exist_ok=True)
    upgrade_head()
    with session_scope() as s:
        seed_sports(s)
    if settings.is_demo:
        with session_scope() as s:
            res = jobs.bootstrap_demo(s, settings)
            log.info("Modo demo: %s", res.get("status"))
    else:
        # Modo real: los datos sintéticos nunca conviven con los reales
        with session_scope() as s:
            jobs.wipe_synthetic(s)
    log.info("QuantSport AI iniciado (modo=%s, entorno=%s)", settings.data_mode, settings.app_env)
    scheduler.start(settings)
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="QuantSport AI",
        version="0.1.0",
        description=(
            "Plataforma de análisis cuantitativo deportivo. Análisis probabilístico, no garantía de resultado. "
            "No ejecuta apuestas ni realiza transacciones."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        log.exception("Error no controlado")
        return JSONResponse(status_code=500, content={"detail": "Error interno. Revisa los logs del servidor."})

    app.include_router(router)
    app.include_router(markets_router)
    app.include_router(scheduler_router)
    return app


app = create_app()
