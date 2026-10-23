"""Valor esperado, edge y cuota mínima.

Definiciones (apuesta binaria, sin devolución, cuota decimal d, probabilidad p):
    EV_por_unidad = p * (d - 1) - (1 - p) = p * d - 1
    EV_porcentual = 100 * (p * d - 1)
    edge          = p_modelo - p_mercado_sin_margen
    d_min(r)      = (1 + r) / p           (EV objetivo r, en decimal)

Un EV positivo es una esperanza matemática condicionada a que p sea fiable; no implica
ganar en cada apuesta. Para mercados con push o medias liquidaciones usar
``app.probability.settlement``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.probability.odds import validate_decimal, validate_probability

DEFINITIONS = {
    "ev": "EV = p_modelo * d - 1 (por unidad apostada; binaria sin devolución)",
    "ev_states": "EV = Σ_s P(s) * retorno_neto(s) sobre todos los estados de liquidación",
    "edge": "edge = p_modelo - p_mercado_sin_margen (mismo mercado, misma línea)",
    "fair_odds": "d_justa = 1 / p_modelo",
    "min_odds": "d_min = (1 + r) / p_modelo, r = EV objetivo",
    "overround": "overround = Σ(1/d_i) - 1",
    "kelly": "f = (p*d - 1) / (d - 1), truncado en 0",
}


def ev_binary(p: float, d: float) -> float:
    p = validate_probability(p, allow_bounds=True)
    d = validate_decimal(d)
    return p * d - 1.0


def ev_percent(p: float, d: float) -> float:
    return 100.0 * ev_binary(p, d)


def edge(p_model: float, p_market_fair: float) -> float:
    return validate_probability(p_model, allow_bounds=True) - validate_probability(
        p_market_fair, allow_bounds=True
    )


def min_odds_for_target(p: float, target_ev: float = 0.0) -> float:
    """Cuota mínima para alcanzar un EV objetivo (binaria sin devolución)."""
    p = validate_probability(p)
    if target_ev <= -1.0:
        raise ValueError("EV objetivo debe ser > -1")
    return (1.0 + target_ev) / p


def min_odds_for_target_states(
    p_win: float, p_push: float, target_ev: float = 0.0, p_half_win: float = 0.0, p_half_loss: float = 0.0
) -> float:
    """Cuota mínima con estados push / medio ganada / medio perdida.

    EV(d) = p_win(d-1) + p_half_win(d-1)/2 - p_half_loss/2 - p_loss
    Lineal en d => se despeja directamente.
    """
    p_loss = 1.0 - p_win - p_push - p_half_win - p_half_loss
    slope = p_win + 0.5 * p_half_win
    if slope <= 0:
        raise ValueError("Probabilidad de ganar nula: no existe cuota mínima")
    # target = slope*(d-1) - 0.5*p_half_loss - p_loss
    return 1.0 + (target_ev + 0.5 * p_half_loss + p_loss) / slope


@dataclass(frozen=True)
class EVInterval:
    point: float
    low: float
    high: float
    prob_positive: float
    level: float


def ev_interval_from_samples(p_samples: Sequence[float], d: float, level: float = 0.9) -> EVInterval:
    """Intervalo del EV propagando la incertidumbre de p (muestras de p)."""
    d = validate_decimal(d)
    arr = np.clip(np.asarray(p_samples, dtype=float), 0.0, 1.0)
    if arr.size == 0:
        raise ValueError("Se requieren muestras de probabilidad")
    evs = arr * d - 1.0
    alpha = (1.0 - level) / 2.0
    return EVInterval(
        point=float(np.mean(evs)),
        low=float(np.quantile(evs, alpha)),
        high=float(np.quantile(evs, 1 - alpha)),
        prob_positive=float(np.mean(evs > 0)),
        level=level,
    )
