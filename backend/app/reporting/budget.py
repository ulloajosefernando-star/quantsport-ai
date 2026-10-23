"""Estimación de costes mensuales en MXN y control de consumo de proveedores.

Precios verificados en las páginas oficiales en oct-2026 (ver docs/ARQUITECTURA_Y_PRESUPUESTO.md).
Los tipos de cambio son parámetros manuales: revísalos antes de contratar cualquier servicio.
La plataforma NUNCA contrata servicios automáticamente.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import DataSourceLog
from app.providers.http import period_start


def cost_table(settings: Settings) -> dict:
    usd, eur = settings.usd_mxn_rate, settings.eur_mxn_rate
    items = [
        {"category": "Datos deportivos", "option": "football-data.org Free + CSV football-data.co.uk",
         "monthly_mxn": 0.0, "status": "recomendado", "notes": "Gratis. Sin Liga MX en football-data.org Free."},
        {"category": "Datos deportivos", "option": "API-Football Free (100 solicitudes/día)",
         "monthly_mxn": 0.0, "status": "opcional", "notes": "Cubre Liga MX, pero solo temporadas pasadas (históricos)."},
        {"category": "Datos deportivos", "option": "API-Football Pro (19 USD)", "monthly_mxn": round(19 * usd, 0),
         "status": "opcional",
         "notes": "7,500 solicitudes/día; según reportes incluye la temporada actual (verificar antes de contratar)."},
        {"category": "Cuotas", "option": "The Odds API Starter (500 créditos/mes)", "monthly_mxn": 0.0,
         "status": "recomendado", "notes": "≈ 2 mercados × 1 región = 2 créditos por liga y consulta."},
        {"category": "Cuotas", "option": "The Odds API 20K (30 USD)", "monthly_mxn": round(30 * usd, 0),
         "status": "excede presupuesto", "notes": "Incluye cuotas históricas; supera 500 MXN con alojamiento."},
        {"category": "Alojamiento", "option": "Modo local (tu computadora)", "monthly_mxn": 0.0,
         "status": "recomendado", "notes": "Solo electricidad y conexión."},
        {"category": "Alojamiento", "option": "VPS Hetzner CPX02 (1 vCPU, 1 GB) 6.49 EUR",
         "monthly_mxn": round(6.49 * eur, 0), "status": "opcional",
         "notes": "Un solo servidor para API, bot, scheduler y BD. Precio sin IVA; verificar antes de contratar."},
        {"category": "Base de datos", "option": "SQLite local / PostgreSQL en el mismo VPS", "monthly_mxn": 0.0,
         "status": "recomendado", "notes": "Sin base de datos gestionada de pago."},
        {"category": "Dominio", "option": "Sin dominio (IP o túnel)", "monthly_mxn": 0.0, "status": "recomendado",
         "notes": "Un dominio .com cuesta aprox. 250–400 MXN/año (≈ 20–35 MXN/mes); opcional."},
        {"category": "Servicios opcionales", "option": "Telegram Bot API", "monthly_mxn": 0.0,
         "status": "recomendado", "notes": "Gratuito."},
        {"category": "Servicios opcionales", "option": "Backups del VPS (+20% del servidor)",
         "monthly_mxn": round(6.49 * eur * 0.2, 0), "status": "opcional", "notes": "Alternativa: pg_dump local."},
    ]
    scenarios = {
        "local": 0.0,
        "vps_minimo": round(6.49 * eur, 0),
        "vps_con_api_football_pro": round(6.49 * eur + 19 * usd, 0),
    }
    return {"budget_mxn": settings.monthly_budget_mxn, "usd_mxn": usd, "eur_mxn": eur, "items": items,
            "scenarios": scenarios,
            "within_budget": {k: v <= settings.monthly_budget_mxn for k, v in scenarios.items()}}


def provider_usage(s: Session, settings: Settings) -> list[dict]:
    limits = {
        "the-odds-api": (settings.the_odds_api_monthly_credits, "month"),
        "football-data.org": (None, "day"),
        "api-football": (settings.api_football_daily_limit, "day"),
    }
    out = []
    for provider, (limit, period) in limits.items():
        start = period_start(period)
        used = s.scalar(select(func.coalesce(func.sum(DataSourceLog.credits_used), 0.0))
                        .where(DataSourceLog.provider == provider, DataSourceLog.created_at >= start)) or 0.0
        calls = s.scalar(select(func.count(DataSourceLog.id))
                         .where(DataSourceLog.provider == provider, DataSourceLog.created_at >= start)) or 0
        errors = s.scalar(select(func.count(DataSourceLog.id))
                          .where(DataSourceLog.provider == provider, DataSourceLog.created_at >= start,
                                 DataSourceLog.status == "error")) or 0
        last = s.scalar(select(func.max(DataSourceLog.created_at)).where(DataSourceLog.provider == provider))
        out.append({"provider": provider, "period": period, "credits_used": float(used), "limit": limit,
                    "calls": int(calls), "errors": int(errors), "last_call": last.isoformat() if last else None,
                    "pct_used": (float(used) / limit) if limit else None})
    return out
