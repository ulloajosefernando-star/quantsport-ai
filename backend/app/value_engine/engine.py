"""Motor de valor: compara la probabilidad del modelo con cuotas reales disponibles.

Categorías (ninguna es garantía de resultado):
    candidato_valor        EV, edge e incertidumbre superan todos los umbrales y el modelo está validado.
    en_observacion         EV puntual positivo y probabilidad razonable de EV>0, sin alcanzar todos los umbrales.
    ventaja_no_confirmada  EV puntual positivo pero incertidumbre alta o modelo no validado.
    sin_valor              EV ≤ 0 o cuota fuera del rango configurado.
    datos_insuficientes    Faltan datos del modelo/equipos o el modelo no pudo ajustarse.
    cuota_desactualizada   La mejor cuota supera la antigüedad máxima permitida.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import numpy as np

from app.bankroll.kelly import kelly_binary, kelly_states, suggest_stake
from app.core.config import ValueThresholds
from app.probability.ev import DEFINITIONS, min_odds_for_target, min_odds_for_target_states
from app.probability.settlement import SettlementDistribution

CATEGORIES = {
    "candidato_valor": "Candidato de valor",
    "en_observacion": "En observación",
    "ventaja_no_confirmada": "Ventaja no confirmada",
    "sin_valor": "Sin valor detectable",
    "datos_insuficientes": "Datos insuficientes",
    "cuota_desactualizada": "Cuota desactualizada",
}
CATEGORY_ORDER = list(CATEGORIES)
WATCH_PROB_POSITIVE = 0.65


@dataclass
class Offer:
    bookmaker: str
    odds: float
    captured_at: datetime
    source: str
    is_synthetic: bool = False

    def age_minutes(self, now: datetime) -> float:
        return (now - self.captured_at).total_seconds() / 60.0


@dataclass
class SelectionInput:
    market: str
    selection: str
    line: float | None
    label: str
    offers: list[Offer]
    p_market_fair: float | None
    # dist_fn(odds) -> distribución de liquidación con la probabilidad PUNTUAL del modelo
    dist_fn: Callable[[float], SettlementDistribution]
    # ev_samples_fn(odds) -> muestras de EV propagando incertidumbre de parámetros
    ev_samples_fn: Callable[[float], np.ndarray]
    binary: bool = True


@dataclass
class Assessment:
    market: str
    selection: str
    line: float | None
    label: str
    category: str
    category_label: str
    bookmaker: str | None = None
    odds: float | None = None
    odds_captured_at: str | None = None
    odds_age_minutes: float | None = None
    odds_source: str | None = None
    p_model: float | None = None
    p_push: float | None = None
    p_model_ci90: list[float] | None = None
    p_market_fair: float | None = None
    fair_odds: float | None = None
    min_odds: float | None = None
    edge: float | None = None
    ev: float | None = None
    ev_ci90: list[float] | None = None
    prob_ev_positive: float | None = None
    robustness: float | None = None
    kelly_full: float | None = None
    kelly_suggested_fraction: float | None = None
    n_offers: int = 0
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    definitions: dict[str, str] = field(default_factory=dict)
    quality_score: float = 0.0
    is_synthetic: bool = False
    model_status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def assess(
    sel: SelectionInput,
    th: ValueThresholds,
    model_status: str,
    data_issues: list[str],
    calibration_score: float,
    now: datetime | None = None,
    p_samples: np.ndarray | None = None,
    sample_score: float | None = None,
) -> Assessment:
    """Evalúa una selección. ``sample_score`` (0-1) resume el tamaño de la muestra de validación."""
    now = now or datetime.now(UTC)
    a = Assessment(market=sel.market, selection=sel.selection, line=sel.line, label=sel.label,
                   category="sin_valor", category_label="", n_offers=len(sel.offers), model_status=model_status)
    a.definitions = {k: DEFINITIONS[k] for k in (("ev", "edge", "fair_odds", "min_odds") if sel.binary
                                                  else ("ev_states", "fair_odds", "min_odds"))}
    a.p_market_fair = sel.p_market_fair

    def finish(cat: str) -> Assessment:
        a.category = cat
        a.category_label = CATEGORIES[cat]
        return a

    if data_issues:
        a.reasons.extend(data_issues)
        return finish("datos_insuficientes")
    if not sel.offers:
        a.reasons.append("No hay cuotas disponibles para esta selección: solo análisis, no oportunidad.")
        return finish("datos_insuficientes")

    best = max(sel.offers, key=lambda o: o.odds)
    fresh = [o for o in sel.offers if o.age_minutes(now) <= th.max_odds_age_minutes]
    a.is_synthetic = any(o.is_synthetic for o in sel.offers)
    if not fresh:
        a.bookmaker, a.odds = best.bookmaker, best.odds
        a.odds_captured_at = best.captured_at.isoformat()
        a.odds_age_minutes = round(best.age_minutes(now), 1)
        a.reasons.append(f"La cuota más reciente tiene {a.odds_age_minutes:.0f} min (máximo {th.max_odds_age_minutes}).")
        return finish("cuota_desactualizada")
    best = max(fresh, key=lambda o: o.odds)
    a.bookmaker, a.odds, a.odds_source = best.bookmaker, best.odds, best.source
    a.odds_captured_at = best.captured_at.isoformat()
    a.odds_age_minutes = round(best.age_minutes(now), 1)

    dist = sel.dist_fn(best.odds)
    a.ev = dist.expected_value
    a.p_model = dist.p_full_win + 0.5 * sum(s.probability for s in dist.states if s.name == "medio_gana")
    a.p_push = dist.p_push if not sel.binary else None
    if sel.binary:
        a.fair_odds = 1.0 / a.p_model if a.p_model > 0 else None
        a.min_odds = min_odds_for_target(a.p_model, th.target_ev) if a.p_model > 0 else None
        if sel.p_market_fair is not None:
            a.edge = a.p_model - sel.p_market_fair
        a.robustness = a.p_model - 1.0 / best.odds  # cuánto puede bajar p antes de EV<0
        a.kelly_full = kelly_binary(a.p_model, best.odds)
    else:
        p_win = dist.p_full_win
        p_half_win = sum(s.probability for s in dist.states if s.name == "medio_gana")
        p_half_loss = sum(s.probability for s in dist.states if s.name == "medio_pierde")
        try:
            a.fair_odds = min_odds_for_target_states(p_win, dist.p_push, 0.0, p_half_win, p_half_loss)
            a.min_odds = min_odds_for_target_states(p_win, dist.p_push, th.target_ev, p_half_win, p_half_loss)
        except ValueError:
            a.fair_odds = a.min_odds = None
        a.robustness = a.ev / max(best.odds, 1.0)
        a.kelly_full = kelly_states(dist)
        a.risks.append("Mercado con devolución/medias liquidaciones: EV calculado sobre todos los estados.")
    if p_samples is not None and len(p_samples) > 1:
        a.p_model_ci90 = [float(np.quantile(p_samples, 0.05)), float(np.quantile(p_samples, 0.95))]

    ev_s = sel.ev_samples_fn(best.odds)
    if ev_s.size > 1:
        a.ev_ci90 = [float(np.quantile(ev_s, 0.05)), float(np.quantile(ev_s, 0.95))]
        a.prob_ev_positive = float(np.mean(ev_s > 0))
    else:
        a.risks.append("Sin estimación de incertidumbre de parámetros.")
        a.prob_ev_positive = None

    stake = suggest_stake(a.kelly_full or 0.0, 1.0, th.kelly_fraction, th.max_stake_fraction)
    a.kelly_suggested_fraction = stake.capped_fraction

    # ------------------------------------------------------------ decisión
    if not (th.min_odds <= best.odds <= th.max_odds):
        a.reasons.append(f"Cuota {best.odds:.2f} fuera del rango configurado [{th.min_odds}, {th.max_odds}].")
        return finish("sin_valor")
    if a.ev <= 0:
        a.reasons.append(f"EV estimado {a.ev:+.1%} con la mejor cuota disponible: sin ventaja.")
        return finish("sin_valor")

    a.quality_score = _quality_score(a, calibration_score, th, sample_score)
    if model_status != "production":
        a.reasons.append(
            f"EV puntual {a.ev:+.1%}, pero el modelo no está validado para este mercado (estado: {model_status})."
        )
        a.risks.append("Las ventajas aparentes de un modelo no validado suelen ser error de estimación.")
        return finish("ventaja_no_confirmada")

    ppos = a.prob_ev_positive if a.prob_ev_positive is not None else 0.0
    passes = {
        "ev": a.ev >= th.min_ev,
        "edge": (a.edge is None and not sel.binary) or (a.edge is not None and a.edge >= th.min_edge),
        "incertidumbre": ppos >= th.min_prob_ev_positive,
    }
    if all(passes.values()):
        a.reasons.append(
            f"EV {a.ev:+.1%} (IC90 {a.ev_ci90[0]:+.1%} a {a.ev_ci90[1]:+.1%}), P(EV>0)={ppos:.0%}"
            + (f", edge {a.edge:+.1%} sobre el mercado sin margen." if a.edge is not None else ".")
        )
        a.risks.append("El EV es una esperanza bajo los supuestos del modelo; resultados individuales varían mucho.")
        return finish("candidato_valor")
    failed = [k for k, v in passes.items() if not v]
    a.reasons.append(f"EV puntual {a.ev:+.1%}; no supera: {', '.join(failed)}.")
    if ppos >= WATCH_PROB_POSITIVE:
        return finish("en_observacion")
    a.risks.append("Incertidumbre elevada: el intervalo del EV incluye valores negativos con frecuencia.")
    return finish("ventaja_no_confirmada")


def _quality_score(a: Assessment, calibration_score: float, th: ValueThresholds,
                   sample_score: float | None = None) -> float:
    """Puntuación SOLO para ordenar dentro de una categoría (no decide la categoría).

    Pesos: frescura de la cuota 0.20, calibración histórica 0.15, magnitud del EV 0.20 (saturada en 10%),
    incertidumbre P(EV>0) 0.20, robustez 0.05, tamaño de muestra 0.10 y disponibilidad de la cuota
    (número de operadores, saturada en 3) 0.10. Una cuota alta por sí sola no sube la puntuación:
    solo cuenta a través del EV, que depende de la probabilidad del modelo.
    """
    fresh = 1.0 - min(1.0, (a.odds_age_minutes or 0) / max(th.max_odds_age_minutes, 1))
    ev_term = min(1.0, max(0.0, (a.ev or 0) / 0.10))
    ppos = a.prob_ev_positive or 0.0
    robust = min(1.0, max(0.0, (a.robustness or 0) / 0.05))
    sample = 0.0 if sample_score is None else min(1.0, max(0.0, sample_score))
    avail = min(1.0, a.n_offers / 3.0)
    return round(0.20 * fresh + 0.15 * calibration_score + 0.20 * ev_term + 0.20 * ppos + 0.05 * robust
                 + 0.10 * sample + 0.10 * avail, 4)
