"""Criterio de Kelly como REFERENCIA matemática. Nunca se convierte en una orden de apuesta.

Kelly binario sin devolución:  f = (p*d - 1) / (d - 1), truncado en 0.
Kelly fraccionado:             f_apuesta = fracción * f.
Kelly por estados:             argmax_f Σ_s P(s) log(1 + f * r_s)  (mercados con push).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import minimize_scalar

from app.probability.odds import validate_decimal, validate_probability
from app.probability.settlement import SettlementDistribution


def kelly_binary(p: float, d: float) -> float:
    p = validate_probability(p, allow_bounds=True)
    d = validate_decimal(d)
    return max(0.0, (p * d - 1.0) / (d - 1.0))


def kelly_states(dist: SettlementDistribution) -> float:
    """Fracción de Kelly para una distribución de liquidación general."""
    if dist.expected_value <= 0:
        return 0.0
    worst = min(s.net_return for s in dist.states)
    upper = 0.999 / abs(worst) if worst < 0 else 0.999

    def neg_growth(f: float) -> float:
        total = 0.0
        for s in dist.states:
            x = 1.0 + f * s.net_return
            if x <= 0:
                return math.inf
            total += s.probability * math.log(x)
        return -total

    res = minimize_scalar(neg_growth, bounds=(0.0, min(upper, 0.999)), method="bounded")
    return max(0.0, float(res.x))


@dataclass(frozen=True)
class StakeSuggestion:
    full_kelly: float
    fraction_used: float
    capped_fraction: float
    stake_amount: float
    capped_by_limit: bool
    warning: str


def suggest_stake(
    full_kelly: float,
    bankroll: float,
    kelly_fraction: float = 0.25,
    max_fraction_per_selection: float = 0.02,
) -> StakeSuggestion:
    """Análisis de tamaño de stake. Informativo: el usuario decide."""
    if bankroll < 0:
        raise ValueError("La banca no puede ser negativa")
    if not 0 <= kelly_fraction <= 1:
        raise ValueError("La fracción de Kelly debe estar entre 0 y 1")
    frac = full_kelly * kelly_fraction
    capped = min(frac, max_fraction_per_selection)
    return StakeSuggestion(
        full_kelly=full_kelly,
        fraction_used=frac,
        capped_fraction=capped,
        stake_amount=round(bankroll * capped, 2),
        capped_by_limit=capped < frac,
        warning=(
            "Referencia matemática basada en probabilidades estimadas con error. "
            "No es una orden de apuesta ni una garantía."
        ),
    )
