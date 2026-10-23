"""Envío de resúmenes por Telegram Bot API (sendMessage).

* Solo a chats en TELEGRAM_ALLOWED_CHAT_IDS (control de acceso).
* Deduplicación por (chat, clave) en la tabla notifications.
* Límite diario de mensajes configurable.
* Nunca envía oportunidades con cuota obsoleta o datos insuficientes.
Los comandos interactivos (/start, /ranking, ...) llegan en la Fase 6.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Notification
from app.value_engine.engine import CATEGORIES

DISCLAIMER = "Análisis probabilístico, no garantía de resultado. El sistema no ejecuta apuestas."
SENDABLE = {"candidato_valor", "en_observacion"}
MAX_LEN = 3900  # límite de Telegram: 4096 caracteres


def _pct(x: float | None) -> str:
    return "n/d" if x is None else f"{x:.1%}"


def format_opportunity(r: dict) -> str:
    unc = "n/d"
    if r.get("ev_ci90"):
        unc = f"EV IC90 [{r['ev_ci90'][0]:+.1%}, {r['ev_ci90'][1]:+.1%}], P(EV>0)={_pct(r.get('prob_ev_positive'))}"
    fair = f"{r['fair_odds']:.2f}" if r.get("fair_odds") else "n/d"
    min_odds = f"{r['min_odds']:.2f}" if r.get("min_odds") else "n/d"
    lines = []
    if r.get("is_synthetic"):
        lines.append("⚠️ DATOS SINTÉTICOS (demo)")
    lines += [
        f"QUANTSPORT AI — RANKING #{r.get('rank', '-')}",
        f"Deporte: {r['sport']} · {r['competition']}",
        f"Evento: {r['event']} ({r['kickoff_local']} CDMX)",
        f"Mercado: {r['label']}",
        f"Cuota observada: {r['odds']:.2f} ({r.get('bookmaker')})",
        f"Probabilidad estimada: {_pct(r.get('p_model'))}",
        f"Probabilidad de mercado sin margen: {_pct(r.get('p_market_fair'))}",
        f"Cuota justa: {fair}",
        f"EV estimado: {_pct(r.get('ev'))}",
        f"Incertidumbre: {unc}",
        f"Calidad de datos: modelo {r.get('model_status')}, muestra {r.get('sample_size')} partidos",
        f"Última actualización: {(r.get('odds_captured_at') or 'n/d')[:16]} UTC",
        f"Decisión del sistema: {CATEGORIES[r['category']]}",
        "Razón principal:",
        "; ".join(r.get("reasons", [])) or "n/d",
        "Riesgos:",
        "; ".join(r.get("risks", [])) or "n/d",
        "Condición:",
        f"Cuota mínima para conservar el EV objetivo: {min_odds}",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def format_summary(rows: list[dict], total_events: int, limit: int = 5) -> list[str]:
    sendable = [r for r in rows if r["category"] in SENDABLE]
    header = (
        f"QUANTSPORT AI — RESUMEN {datetime.now(UTC).strftime('%Y-%m-%d')}\n"
        f"Eventos analizados: {total_events}\n"
        f"Candidatos de valor: {sum(r['category'] == 'candidato_valor' for r in rows)} · "
        f"En observación: {sum(r['category'] == 'en_observacion' for r in rows)}\n"
    )
    if not sendable:
        return [header + "\nSin oportunidades que superen los controles. Recomendación: NO BET.\n\n" + DISCLAIMER]
    msgs = [header]
    for r in sendable[:limit]:
        msgs.append(format_opportunity(r)[:MAX_LEN])
    return msgs


class TelegramNotifier:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_ids)

    def _sent_today(self, s: Session) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(s.scalar(select(func.count(Notification.id)).where(
            Notification.status == "sent", Notification.created_at >= start)) or 0)

    def send(self, s: Session, chat_id: str, text: str, dedupe_key: str | None = None) -> str:
        if chat_id not in self.settings.telegram_chat_ids:
            return "denied"
        key = dedupe_key or hashlib.sha256(text.encode()).hexdigest()[:32]
        if s.scalar(select(Notification.id).where(Notification.chat_id == chat_id, Notification.dedupe_key == key,
                                                  Notification.status == "sent")):
            return "duplicate"
        if self._sent_today(s) >= self.settings.telegram_max_messages_per_day:
            return "rate_limited"
        n = s.scalar(select(Notification).where(Notification.chat_id == chat_id, Notification.dedupe_key == key))
        if n is None:
            n = Notification(chat_id=chat_id, dedupe_key=key, status="pending", body=text)
            s.add(n)
            try:
                s.flush()
            except IntegrityError:
                s.rollback()
                return "duplicate"
        try:
            with httpx.Client(transport=self.transport, timeout=15) as c:
                resp = c.post(f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
                              json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
            if resp.status_code == 200 and resp.json().get("ok"):
                n.status, n.error = "sent", ""
            else:
                n.status, n.error = "error", resp.text[:500]
        except httpx.HTTPError as exc:
            n.status, n.error = "error", str(exc)[:500]
        s.commit()
        return n.status

    def send_summary(self, s: Session, rows: list[dict], total_events: int) -> dict:
        if not self.configured:
            return {"status": "not_configured",
                    "detail": "Configura TELEGRAM_BOT_TOKEN y TELEGRAM_ALLOWED_CHAT_IDS en .env"}
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        results = {}
        for chat in sorted(self.settings.telegram_chat_ids):
            for i, msg in enumerate(format_summary(rows, total_events)):
                results[f"{chat}:{i}"] = self.send(s, chat, msg, dedupe_key=f"summary:{day}:{i}:"
                                                   + hashlib.sha256(msg.encode()).hexdigest()[:12])
        return {"status": "done", "results": results}
