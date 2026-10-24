"""Cliente HTTP compartido por los adaptadores reales.

* Caché en disco con TTL (la clave excluye credenciales).
* Límite de solicitudes por minuto (espaciado mínimo entre llamadas).
* Presupuesto de créditos por periodo (día/mes) consultando el registro DataSourceLog.
* Reintentos con backoff exponencial para 429/5xx/timeouts.
* Cada llamada se registra (proveedor, endpoint, estado, créditos, latencia, error).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select

from app.core.logging import get_logger, redact
from app.db.models import DataSourceLog
from app.db.session import session_scope
from app.providers.base import ProviderError, QuotaExceeded

log = get_logger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class QuotaPolicy:
    limit: float  # créditos por periodo
    period: str  # "day" | "month"


def period_start(period: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(period)


def credits_used(provider: str, period: str) -> float:
    with session_scope() as s:
        total = s.scalar(
            select(func.coalesce(func.sum(DataSourceLog.credits_used), 0.0)).where(
                DataSourceLog.provider == provider, DataSourceLog.created_at >= period_start(period)
            )
        )
    return float(total or 0.0)


def record_call(provider: str, endpoint: str, status: str, **kw: Any) -> None:
    try:
        with session_scope() as s:
            s.add(DataSourceLog(provider=provider, endpoint=redact(endpoint)[:256], status=status, **kw))
    except Exception:  # el registro nunca debe tumbar la ingesta
        log.exception("No se pudo registrar la llamada al proveedor")


class ProviderHttpClient:
    def __init__(
        self,
        provider: str,
        base_url: str,
        cache_dir: Path,
        headers: dict[str, str] | None = None,
        per_minute: int = 10,
        quota: QuotaPolicy | None = None,
        max_retries: int = 4,
        backoff_base: float = 2.0,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.quota = quota
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.min_interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._last_call = 0.0
        self._lock = threading.Lock()
        self._sleep = sleep
        self.cache_dir = cache_dir / provider
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(base_url=base_url, headers=headers or {}, timeout=timeout, transport=transport)

    # ---------------------------------------------------------------- cache
    def _cache_path(self, path: str, params: dict[str, Any]) -> Path:
        safe = {k: v for k, v in sorted(params.items()) if k.lower() not in {"apikey", "api_key", "token"}}
        key = hashlib.sha256(f"{path}?{json.dumps(safe, sort_keys=True)}".encode()).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, p: Path, ttl: float) -> Any | None:
        if ttl <= 0 or not p.exists():
            return None
        payload = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - payload["stored_at"] > ttl:
            return None
        return payload

    # ---------------------------------------------------------------- calls
    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()

    def check_quota(self, cost: float) -> None:
        if not self.quota:
            return
        used = credits_used(self.provider, self.quota.period)
        if used + cost > self.quota.limit:
            record_call(self.provider, "(bloqueada por presupuesto)", "blocked", credits_used=0.0)
            raise QuotaExceeded(
                f"{self.provider}: consumo {used:.0f}+{cost:.0f} supera el límite "
                f"{self.quota.limit:.0f} por {'día' if self.quota.period == 'day' else 'mes'}"
            )

    def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        cache_ttl: float = 600,
        cost: float = 1.0,
        credits_from_headers: Callable[[httpx.Response], tuple[float, float | None]] | None = None,
        validate: Callable[[Any], None] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """Devuelve (json, meta). meta incluye 'from_cache' y 'fetched_at' (UTC ISO).

        ``validate`` revisa el cuerpo de una respuesta 200 antes de guardarla en caché; si lanza
        ProviderError, la llamada se registra como error y no se cachea (p. ej. errores de plan).
        """
        params = params or {}
        cpath = self._cache_path(path, params)
        cached = self._read_cache(cpath, cache_ttl)
        if cached is not None:
            record_call(self.provider, path, "cache", credits_used=0.0)
            return cached["data"], {"from_cache": True, "fetched_at": cached["fetched_at"]}

        self.check_quota(cost)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            started = time.perf_counter()
            try:
                resp = self.client.get(path, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
                record_call(self.provider, path, "error", error=str(exc)[:500])
            else:
                latency = (time.perf_counter() - started) * 1000
                used, remaining = (cost, None)
                if credits_from_headers:
                    used, remaining = credits_from_headers(resp)
                if resp.status_code == 200:
                    data = resp.json()
                    if validate:
                        try:
                            validate(data)
                        except ProviderError as exc:
                            record_call(
                                self.provider, path, "error", http_status=200, credits_used=used,
                                credits_remaining=remaining, latency_ms=latency, error=str(exc)[:500],
                            )
                            raise
                    fetched_at = datetime.now(UTC).isoformat()
                    cpath.write_text(
                        json.dumps({"stored_at": time.time(), "fetched_at": fetched_at, "data": data}),
                        encoding="utf-8",
                    )
                    record_call(
                        self.provider, path, "ok", http_status=200, credits_used=used,
                        credits_remaining=remaining, latency_ms=latency,
                    )
                    return data, {"from_cache": False, "fetched_at": fetched_at, "credits_remaining": remaining}
                record_call(
                    self.provider, path, "error", http_status=resp.status_code, credits_used=used,
                    credits_remaining=remaining, latency_ms=latency, error=resp.text[:500],
                )
                if resp.status_code not in RETRY_STATUS:
                    raise ProviderError(f"{self.provider}: HTTP {resp.status_code} en {path}")
                last_exc = ProviderError(f"HTTP {resp.status_code}")
            if attempt < self.max_retries:
                delay = self.backoff_base ** (attempt + 1)
                log.warning("Reintentando %s %s en %.0fs", self.provider, path, delay)
                self._sleep(delay)
        raise ProviderError(f"{self.provider}: fallo tras {self.max_retries + 1} intentos ({last_exc})")
