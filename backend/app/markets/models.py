"""Adaptadores de modelos probabilísticos al registro de mercados.

Un modelo declara qué mercados puede evaluar de forma fiable (``support``) y, para ellos,
da la distribución de liquidación a una cuota dada y muestras del EV que reflejan la
incertidumbre de sus parámetros. Si no puede evaluar un mercado devuelve el motivo: nunca
inventa una probabilidad.

Estado actual:
    fútbol        Dixon-Coles (matriz de marcadores). Mercados derivados del marcador final.
    NBA, NFL, MLB, tenis   sin modelo todavía (Fase 2): se muestran implícita y margen, sin EV.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.markets import registry
from app.markets.settlement import Outcome, settle
from app.probability.settlement import SettlementDistribution, aggregate, binary_distribution
from app.sports.football.scores import ScoreMatrix

NO_MODEL = {
    "nba": "Sin modelo de NBA todavía (Fase 2). Se muestran probabilidad implícita y margen, sin EV del modelo.",
    "nfl": "Sin modelo de NFL todavía (Fase 2). Se muestran probabilidad implícita y margen, sin EV "
                         "del modelo.",
    "mlb": "Sin modelo de MLB todavía (Fase 2). Se muestran probabilidad implícita y margen, sin EV del modelo.",
    "tennis": "Sin modelo de tenis todavía (Fase 2). Se muestran probabilidad implícita y margen, sin EV del modelo.",
}

# Mercados que se liquidan solo con el marcador final y por tanto salen de la matriz de marcadores
SCORE_MARKETS = {"1x2", "double_chance", "dnb", "european_handicap", "ah", "totals", "team_totals", "btts",
                 "correct_score"}
FOOTBALL_UNSUPPORTED = {
    "ht_result": "El modelo se ajusta con marcadores finales; no tiene goles por tiempo calibrados.",
    "ht_ft": "El modelo se ajusta con marcadores finales; no tiene goles por tiempo calibrados.",
    "corners_totals": "Las fuentes actuales no traen córners: no hay datos para modelarlos.",
    "cards_totals": "Las fuentes actuales no traen tarjetas: no hay datos para modelarlas.",
    "next_goal": "Mercado en vivo: la plataforma no tiene modelo en vivo.",
}


@dataclass(frozen=True)
class SelectionModel:
    """Lo que un modelo aporta para una selección concreta."""

    dist_fn: object  # Callable[[float], SettlementDistribution]
    ev_samples_fn: object  # Callable[[float], np.ndarray]
    p_samples: np.ndarray | None  # muestras de P(gana) si el mercado no tiene devolución
    binary: bool
    assumption: str = ""


class MarketModel:
    sport: str = ""
    name: str = ""

    def support(self, m: registry.MarketDef, period: str, is_live: bool) -> str | None:
        """None si el modelo puede evaluar el mercado; si no, el motivo."""
        raise NotImplementedError

    def selection_model(self, m: registry.MarketDef, selection: str, line: float | None) -> SelectionModel:
        raise NotImplementedError

    def market_status(self, market: str, line: float | None) -> str:
        return "experimental"


@lru_cache(maxsize=4096)
def _cell_returns(n: int, rule: str, selection: str, line: float | None, odds: float) -> tuple[np.ndarray, np.ndarray]:
    names = np.empty((n, n), dtype=object)
    rets = np.zeros((n, n))
    for h in range(n):
        for a in range(n):
            names[h, a], rets[h, a] = settle(rule, selection, line, odds, Outcome(h, a))
    return names, rets


def _norm(sm: ScoreMatrix) -> np.ndarray:
    return sm.matrix / sm.matrix.sum()


def matrix_distribution(sm: ScoreMatrix, rule: str, selection: str, line: float | None,
                        odds: float) -> SettlementDistribution:
    """Distribución de liquidación de cualquier mercado de marcador final a partir de la matriz."""
    m = _norm(sm)
    names, rets = _cell_returns(m.shape[0], rule, selection, line, round(float(odds), 6))
    return aggregate(((float(m[h, a]), names[h, a], float(rets[h, a])) for h in range(m.shape[0])
                      for a in range(m.shape[1])), f"{rule} {selection} {'' if line is None else f'{line:+g}'}")


def matrix_ev(sm: ScoreMatrix, rule: str, selection: str, line: float | None, odds: float) -> float:
    _, rets = _cell_returns(sm.matrix.shape[0], rule, selection, line, round(float(odds), 6))
    return float((_norm(sm) * rets).sum())


def first_to_score_probs(sm: ScoreMatrix) -> dict[str, float]:
    """Supuesto: goles como procesos de Poisson independientes durante el partido.

    P(nadie marca) = P(0-0) de la matriz; P(local marca primero) = λ_local / (λ_local + λ_visitante) · (1 - P(0-0)).
    """
    p00 = float(_norm(sm)[0, 0])
    share = sm.lambda_home / (sm.lambda_home + sm.lambda_away)
    return {"home": share * (1 - p00), "away": (1 - share) * (1 - p00), "none": p00}


class FootballMatrixModel(MarketModel):
    sport = "football"
    name = "Dixon-Coles"

    def __init__(self, sm: ScoreMatrix, sampled: list[ScoreMatrix], gate=None) -> None:
        self.sm, self.sampled, self.gate = sm, sampled, gate
        # Matrices muestreadas apiladas (N, n, n) para calcular EV y P(gana) de todas las muestras de una vez
        n = sm.matrix.shape[0]
        same = [x for x in sampled if x.matrix.shape == (n, n)]
        self._stack = np.stack([_norm(x) for x in same]) if same else np.zeros((0, n, n))

    def support(self, m: registry.MarketDef, period: str, is_live: bool) -> str | None:
        if m.sport != "football":
            return "Mercado de otro deporte"
        if is_live:
            return "Cuota en vivo: la plataforma no tiene modelo en vivo."
        if m.key in FOOTBALL_UNSUPPORTED:
            return FOOTBALL_UNSUPPORTED[m.key]
        if period != "ft":
            return "El modelo solo cubre el partido completo."
        if m.key in SCORE_MARKETS or m.key == "first_team_to_score":
            return None
        return "Mercado sin modelo para fútbol."

    def market_status(self, market: str, line: float | None) -> str:
        return self.gate.market_status(market, line) if self.gate else "experimental"

    def selection_model(self, m: registry.MarketDef, selection: str, line: float | None) -> SelectionModel:
        if m.key == "first_team_to_score":
            p = first_to_score_probs(self.sm)[selection]
            ps = np.array([first_to_score_probs(x)[selection] for x in self.sampled])
            return SelectionModel(lambda d: binary_distribution(p, d), lambda d: ps * d - 1, ps, True,
                                  "Supone goles como procesos de Poisson independientes (experimental).")
        rule = m.settlement
        sm, stack, n = self.sm, self._stack, self.sm.matrix.shape[0]
        # ¿Puede haber devolución o media liquidación? Se mira la distribución a una cuota de referencia.
        ref = matrix_distribution(sm, rule, selection, line, 2.0)
        binary = all(s.name in ("gana", "pierde") for s in ref.states)
        names, _ = _cell_returns(n, rule, selection, line, 2.0)
        win = (names == "gana").astype(float)
        ps = np.tensordot(stack, win, axes=([1, 2], [0, 1])) if binary else None

        def ev_samples(d: float) -> np.ndarray:
            _, rets = _cell_returns(n, rule, selection, line, round(float(d), 6))
            return np.tensordot(stack, rets, axes=([1, 2], [0, 1]))

        return SelectionModel(
            dist_fn=lambda d: matrix_distribution(sm, rule, selection, line, d),
            ev_samples_fn=ev_samples, p_samples=ps, binary=binary)


def no_model_reason(sport: str) -> str:
    return NO_MODEL.get(sport, f"Sin modelo para {registry.SPORT_LABELS.get(sport, sport)}.")


def coverage() -> dict[str, dict[str, str | None]]:
    """Para cada deporte y mercado: None si hay modelo, o el motivo por el que no lo hay."""
    out: dict[str, dict[str, str | None]] = {}
    for sport in registry.sports():
        out[sport] = {}
        for m in registry.for_sport(sport):
            if sport != "football":
                out[sport][m.key] = no_model_reason(sport)
            elif m.key in FOOTBALL_UNSUPPORTED:
                out[sport][m.key] = FOOTBALL_UNSUPPORTED[m.key]
            else:
                out[sport][m.key] = None
    return out
