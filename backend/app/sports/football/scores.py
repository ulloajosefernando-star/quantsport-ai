"""Matriz de marcadores y mercados derivados para fútbol.

Espacio de resultados: pares (goles_local, goles_visitante) ∈ {0..N}². La masa que cae
fuera de la matriz (cola) se reporta explícitamente; con N=10 y goles esperados
habituales es despreciable (<1e-6), pero se comprueba en cada cálculo.

Con rho = 0 la matriz asume independencia condicional de goles (Poisson doble).
Con rho != 0 aplica la corrección de Dixon-Coles a los marcadores 0-0, 1-0, 0-1, 1-1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

from app.probability.settlement import SettlementDistribution, aggregate, handicap_return, total_return

DEFAULT_MAX_GOALS = 10
TAIL_WARNING = 1e-4


def dc_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def rho_bounds(lam: float, mu: float) -> tuple[float, float]:
    """Rango de rho que mantiene todas las tau positivas."""
    lo = max(-1.0 / lam, -1.0 / mu)
    hi = min(1.0 / (lam * mu), 1.0)
    return lo, hi


@dataclass(frozen=True)
class ScoreMatrix:
    matrix: np.ndarray  # [goles_local, goles_visitante]
    lambda_home: float
    lambda_away: float
    rho: float
    tail_mass: float

    @property
    def max_goals(self) -> int:
        return self.matrix.shape[0] - 1

    @property
    def goal_diff_index(self) -> np.ndarray:
        n = self.matrix.shape[0]
        return np.subtract.outer(np.arange(n), np.arange(n))

    @property
    def total_index(self) -> np.ndarray:
        n = self.matrix.shape[0]
        return np.add.outer(np.arange(n), np.arange(n))


def score_matrix(lam: float, mu: float, rho: float = 0.0, max_goals: int = DEFAULT_MAX_GOALS) -> ScoreMatrix:
    if lam <= 0 or mu <= 0:
        raise ValueError("Los goles esperados deben ser positivos")
    lo, hi = rho_bounds(lam, mu)
    if not lo <= rho <= hi:
        raise ValueError(f"rho={rho} fuera del rango válido [{lo:.3f}, {hi:.3f}] para estos lambdas")
    goals = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))
    if rho != 0.0:
        for x in (0, 1):
            for y in (0, 1):
                m[x, y] *= dc_tau(x, y, lam, mu, rho)
    tail = float(1.0 - m.sum())
    return ScoreMatrix(matrix=m, lambda_home=lam, lambda_away=mu, rho=rho, tail_mass=tail)


def _normalized(sm: ScoreMatrix) -> np.ndarray:
    # La cola se reparte proporcionalmente; se reporta aparte para transparencia.
    return sm.matrix / sm.matrix.sum()


def probs_1x2(sm: ScoreMatrix) -> dict[str, float]:
    m = _normalized(sm)
    diff = sm.goal_diff_index
    return {
        "home": float(m[diff > 0].sum()),
        "draw": float(m[diff == 0].sum()),
        "away": float(m[diff < 0].sum()),
    }


def probs_double_chance(sm: ScoreMatrix) -> dict[str, float]:
    p = probs_1x2(sm)
    return {"1X": p["home"] + p["draw"], "12": p["home"] + p["away"], "X2": p["draw"] + p["away"]}


def probs_totals(sm: ScoreMatrix, line: float) -> dict[str, float]:
    """Over/Under para líneas .5 (sin push). Para líneas enteras o asiáticas usar settlement."""
    m = _normalized(sm)
    tot = sm.total_index
    under = float(m[tot < line].sum())
    # La cola corresponde a marcadores altos: se asigna al Over.
    over = 1.0 - under
    return {"over": over, "under": under}


def probs_btts(sm: ScoreMatrix) -> dict[str, float]:
    m = _normalized(sm)
    yes = float(m[1:, 1:].sum())
    return {"yes": yes, "no": 1.0 - yes}


def probs_team_total(sm: ScoreMatrix, team: str, line: float) -> dict[str, float]:
    m = _normalized(sm)
    marg = m.sum(axis=1) if team == "home" else m.sum(axis=0)
    goals = np.arange(len(marg))
    under = float(marg[goals < line].sum())
    return {"over": 1.0 - under, "under": under}


def top_scores(sm: ScoreMatrix, n: int = 6) -> list[dict[str, float | int]]:
    m = _normalized(sm)
    flat = np.argsort(m, axis=None)[::-1][:n]
    out = []
    for idx in flat:
        h, a = np.unravel_index(idx, m.shape)
        out.append({"home": int(h), "away": int(a), "p": float(m[h, a])})
    return out


def handicap_distribution(sm: ScoreMatrix, side: str, line: float, odds: float) -> SettlementDistribution:
    """Hándicap asiático: side 'home'|'away', line aplicada a esa selección."""
    m = _normalized(sm)
    diff = sm.goal_diff_index if side == "home" else -sm.goal_diff_index
    outcomes = []
    for d in np.unique(diff):
        p = float(m[diff == d].sum())
        name, ret = handicap_return(int(d), line, odds)
        outcomes.append((p, name, ret))
    return aggregate(outcomes, f"Hándicap asiático {side} {line:+g}: liquidación por diferencia de goles")


def draw_no_bet_distribution(sm: ScoreMatrix, side: str, odds: float) -> SettlementDistribution:
    return handicap_distribution(sm, side, 0.0, odds)


def totals_distribution(sm: ScoreMatrix, side: str, line: float, odds: float) -> SettlementDistribution:
    m = _normalized(sm)
    tot = sm.total_index
    outcomes = []
    for t in np.unique(tot):
        p = float(m[tot == t].sum())
        name, ret = total_return(int(t), line, odds, side)
        outcomes.append((p, name, ret))
    return aggregate(outcomes, f"Total {side} {line:g}: liquidación por goles totales")


def summary(sm: ScoreMatrix) -> dict:
    return {
        "lambda_home": sm.lambda_home,
        "lambda_away": sm.lambda_away,
        "rho": sm.rho,
        "tail_mass": sm.tail_mass,
        "tail_warning": sm.tail_mass > TAIL_WARNING,
        "1x2": probs_1x2(sm),
        "double_chance": probs_double_chance(sm),
        "totals": {f"{line:g}": probs_totals(sm, line) for line in (0.5, 1.5, 2.5, 3.5, 4.5)},
        "btts": probs_btts(sm),
        "team_totals": {
            "home": {f"{line:g}": probs_team_total(sm, "home", line) for line in (0.5, 1.5, 2.5)},
            "away": {f"{line:g}": probs_team_total(sm, "away", line) for line in (0.5, 1.5, 2.5)},
        },
        "top_scores": top_scores(sm),
    }
