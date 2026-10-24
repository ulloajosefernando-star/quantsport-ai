"""Evaluación de un mercado de un operador: implícita, margen, sin margen, modelo, cuota justa, edge y EV.

Fórmulas (ver docs/CUOTAS_Y_MERCADOS.md):
    implícita            p_i = 1 / d_i
    margen (overround)   Σ 1/d_i - 1                 solo si las selecciones son excluyentes y exhaustivas
    sin margen           método configurable: proporcional, potencia o Shin (app.probability.margin)
    cuota justa          1 / p_modelo  (con devolución: la cuota que deja EV = 0 sobre todos los estados)
    edge                 p_modelo - p_sin_margen      solo mismo mercado, línea y periodo, y sin devolución
    EV binario           p · d - 1
    EV con estados       Σ P(estado) · retorno_neto(estado)

La categoría (candidato, observación, NO BET...) la decide ``app.value_engine.engine.assess``, que exige
modelo validado, EV, edge e incertidumbre; una cuota alta por sí sola nunca la mejora.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.config import ValueThresholds
from app.markets.models import MarketModel
from app.markets.quotes import MarketBook, as_dict
from app.probability.margin import METHODS, compare_methods, remove_margin
from app.probability.odds import FORMATS, format_odds
from app.value_engine.engine import CATEGORIES, Offer, SelectionInput, assess

NOT_EXCLUSIVE = ("Las selecciones de este mercado no son excluyentes y exhaustivas (p. ej. doble oportunidad, "
                 "resultado exacto o goles por equipo): el margen no se puede calcular.")


def uncertainty_level(p_ci: Sequence[float] | None, ev_ci: Sequence[float] | None) -> str:
    """baja / media / alta según el ancho del intervalo del 90% (de p o, si no hay, del EV)."""
    if p_ci:
        w = p_ci[1] - p_ci[0]
        return "baja" if w < 0.06 else "media" if w < 0.12 else "alta"
    if ev_ci:
        w = ev_ci[1] - ev_ci[0]
        return "baja" if w < 0.10 else "media" if w < 0.20 else "alta"
    return "desconocida"


def margin_info(book: MarketBook, method: str = "proportional") -> tuple[dict[str, float], dict]:
    """Probabilidades sin margen por selección (vacío si no se puede) y un resumen del margen."""
    if method not in METHODS:
        raise ValueError(f"Método de margen desconocido: {method}. Opciones: {', '.join(METHODS)}")
    m = book.definition
    present = [s.selection for s, _ in book.entries]
    if not m.exhaustive or not m.selections:
        return {}, {"available": False, "note": NOT_EXCLUSIVE}
    missing = [x for x in m.selections if x not in present]
    if missing:
        return {}, {"available": False,
                    "note": f"Faltan selecciones del mismo operador ({', '.join(missing)}): sin margen calculable."}
    odds = [q.odds_decimal for _, q in book.entries]
    cmp = compare_methods(odds)
    fair = dict(zip(present, remove_margin(odds, method), strict=True))
    return fair, {"available": True, "overround": cmp.overround, "method": method,
                  "method_disagreement": cmp.max_disagreement, "note": cmp.note}


def evaluate_book(book: MarketBook, model: MarketModel | None, no_model_reason: str | None, th: ValueThresholds,
                  margin_method: str = "proportional", now: datetime | None = None,
                  data_issues: Sequence[str] = (), calibration_score: float = 0.0,
                  sample_score: float | None = None) -> dict:
    now = now or datetime.now(UTC)
    m, mk = book.definition, book.market
    fair, margin = margin_info(book, margin_method)
    reason = no_model_reason
    if reason is None and model is not None:
        reason = model.support(m, mk.period, mk.is_live)
    elif reason is None:
        reason = "Sin modelo para este mercado."
    if mk.status != "open":
        reason = f"Mercado {mk.status}: no se evalúa."
    rows = []
    for sref, q in book.entries:
        age = (now - q.updated_at).total_seconds() / 60.0
        row: dict = {
            "selection": as_dict(sref), "quote": as_dict(q),
            "display": {f: format_odds(q.odds_decimal, f) for f in FORMATS},
            "implied_probability": q.implied_probability, "p_no_vig": fair.get(sref.selection),
            "p_model": None, "p_push": None, "p_model_ci90": None, "fair_odds": None, "min_odds": None,
            "edge": None, "ev": None, "ev_ci90": None, "prob_ev_positive": None, "kelly_suggested_fraction": None,
            "category": "datos_insuficientes", "category_label": CATEGORIES["datos_insuficientes"],
            "model_status": "sin_modelo", "quality_score": 0.0, "reasons": [], "risks": [],
        }
        if reason:
            row["reasons"].append(reason)
        else:
            smod = model.selection_model(m, sref.selection, mk.line)
            si = SelectionInput(
                market=m.key, selection=sref.selection, line=mk.line, label=sref.label,
                offers=[Offer(q.bookmaker, q.odds_decimal, q.updated_at, q.source, q.is_synthetic)],
                p_market_fair=fair.get(sref.selection) if smod.binary else None,
                dist_fn=smod.dist_fn, ev_samples_fn=smod.ev_samples_fn, binary=smod.binary)
            a = assess(si, th, model.market_status(m.key, mk.line), list(data_issues), calibration_score, now=now,
                       p_samples=smod.p_samples, sample_score=sample_score)
            for k in ("p_model", "p_push", "p_model_ci90", "fair_odds", "min_odds", "edge", "ev", "ev_ci90",
                      "prob_ev_positive", "kelly_suggested_fraction", "category", "category_label", "model_status",
                      "quality_score", "reasons", "risks"):
                row[k] = getattr(a, k)
            if smod.assumption:
                row["risks"].append(smod.assumption)
            if fair.get(sref.selection) is not None and not smod.binary:
                row["risks"].append("Mercado con devolución: el edge frente al mercado sin margen no aplica; usa el EV.")
        row["uncertainty"] = uncertainty_level(row["p_model_ci90"], row["ev_ci90"])
        row["data_quality"] = {
            "odds_age_minutes": round(age, 1), "stale": age > th.max_odds_age_minutes,
            "is_synthetic": q.is_synthetic or book.event.is_synthetic, "margin_available": margin["available"],
            "model": getattr(model, "name", None) if not reason else None, "issues": list(data_issues),
        }
        rows.append(row)
    return {"event": as_dict(book.event), "market": as_dict(mk), "bookmaker": book.bookmaker,
            "group": m.group, "margin": margin, "model_note": reason, "selections": rows}
