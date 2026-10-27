"""Modelo de puntos NBA: ratings ofensivo y defensivo por equipo con ventaja de local.

Cada partido aporta dos ecuaciones (puntos del local y del visitante)::

    puntos_local     = μ + v/2 + ataque[local]     - defensa[visitante]
    puntos_visitante = μ - v/2 + ataque[visitante] - defensa[local]

Se resuelven por mínimos cuadrados ponderados con penalización ridge sobre los ratings (encoge equipos con
poca muestra hacia la media) y pesos que decaen con la antigüedad del partido (vida media configurable).
La diferencia y el total finales se modelan como normales discretizadas a enteros, con la desviación de los
residuos ponderados. En la NBA no hay empates (hay prórroga): la masa en diferencia 0 se reparte.

La incertidumbre de parámetros sale de la covarianza aproximada de la solución ridge y se usa para muestrear
las medias; no cubre lesiones, descansos ni cambios de plantilla, que el modelo no ve.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import norm

MODEL_NAME = "nba-ratings"
MODEL_VERSION = "1.0"
FEATURES = ["puntos anotados y recibidos", "localía", "fecha del partido"]


class InsufficientDataError(ValueError):
    pass


@dataclass(frozen=True)
class NbaConfig:
    half_life_days: float = 120.0
    ridge: float = 4.0
    max_history_days: int = 3 * 365
    min_team_games: int = 10


@dataclass(frozen=True)
class NbaPrediction:
    home: str
    away: str
    mu_home: float
    mu_away: float
    sd_margin: float
    sd_total: float
    sd_team: float
    se_margin: float  # error estándar de la media de la diferencia (incertidumbre de parámetros)
    se_total: float

    @property
    def mu_margin(self) -> float:
        return self.mu_home - self.mu_away

    @property
    def mu_total(self) -> float:
        return self.mu_home + self.mu_away


def _grid(mu: float | np.ndarray, sd: float, lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
    """Probabilidad de cada entero en [lo, hi] (normal discretizada, renormalizada)."""
    k = np.arange(lo, hi + 1)
    mu = np.atleast_1d(np.asarray(mu, dtype=float))[:, None]
    p = norm.cdf((k + 0.5 - mu) / sd) - norm.cdf((k - 0.5 - mu) / sd)
    return k, p / p.sum(axis=1, keepdims=True)


def margin_distribution(mu: float | np.ndarray, sd: float) -> tuple[np.ndarray, np.ndarray]:
    """Diferencia final local - visitante, sin empates (la prórroga los resuelve)."""
    k, p = _grid(mu, sd, -70, 70)
    keep = k != 0
    p = p[:, keep]
    return k[keep], p / p.sum(axis=1, keepdims=True)


def total_distribution(mu: float | np.ndarray, sd: float) -> tuple[np.ndarray, np.ndarray]:
    return _grid(mu, sd, 120, 340)


def team_distribution(mu: float | np.ndarray, sd: float) -> tuple[np.ndarray, np.ndarray]:
    return _grid(mu, sd, 50, 190)


def p_home_win(mu_margin: float | np.ndarray, sd: float) -> np.ndarray:
    k, p = margin_distribution(mu_margin, sd)
    return (p * (k > 0)).sum(axis=1)


@dataclass
class NbaModel:
    teams: list[str]
    attack: np.ndarray
    defense: np.ndarray
    mu: float
    home_adv: float
    sd_margin: float
    sd_total: float
    sd_team: float
    cov: np.ndarray
    games_per_team: dict[str, float]
    train_start: str
    train_end: str
    n_games: int
    config: NbaConfig
    name: str = MODEL_NAME
    version: str = MODEL_VERSION
    features: list[str] = field(default_factory=lambda: list(FEATURES))

    def _ix(self, team: str) -> int:
        return self.teams.index(team)

    def has_team(self, team: str) -> bool:
        return team in self.teams

    def team_sample_ok(self, team: str) -> bool:
        return self.games_per_team.get(team, 0) >= self.config.min_team_games

    def _row(self, att: str, dfn: str, home_sign: float) -> np.ndarray:
        n = len(self.teams)
        r = np.zeros(2 + 2 * n)
        r[0], r[1] = 1.0, home_sign / 2
        r[2 + self._ix(att)] = 1.0
        r[2 + n + self._ix(dfn)] = -1.0
        return r

    def predict(self, home: str, away: str) -> NbaPrediction:
        rh, ra = self._row(home, away, 1.0), self._row(away, home, -1.0)
        theta = np.concatenate([[self.mu, self.home_adv], self.attack, self.defense])
        cm, ct = rh - ra, rh + ra
        return NbaPrediction(
            home=home, away=away, mu_home=float(rh @ theta), mu_away=float(ra @ theta),
            sd_margin=self.sd_margin, sd_total=self.sd_total, sd_team=self.sd_team,
            se_margin=float(np.sqrt(max(cm @ self.cov @ cm, 0.0))),
            se_total=float(np.sqrt(max(ct @ self.cov @ ct, 0.0))),
        )

    def metadata(self) -> dict:
        fp = hashlib.sha1(
            f"{self.train_start}|{self.train_end}|{self.n_games}|{self.mu:.6f}|{self.home_adv:.6f}".encode()
        ).hexdigest()[:12]
        return {
            "name": self.name, "version": self.version, "fingerprint": fp, "train_start": self.train_start,
            "train_end": self.train_end, "n_games": self.n_games, "n_teams": len(self.teams),
            "league_avg_points": round(self.mu, 2), "home_advantage_points": round(self.home_adv, 2),
            "sd_margin": round(self.sd_margin, 2), "sd_total": round(self.sd_total, 2),
            "half_life_days": self.config.half_life_days, "features": self.features,
        }


def fit(games: pd.DataFrame, now: datetime | pd.Timestamp, cfg: NbaConfig | None = None) -> NbaModel:
    """Ajusta el modelo con partidos ANTERIORES a ``now``.

    ``games``: columnas kickoff (UTC), home, away, home_pts, away_pts.
    """
    cfg = cfg or NbaConfig()
    now = pd.Timestamp(now)
    df = games[(games["kickoff"] < now) & (games["kickoff"] >= now - pd.Timedelta(days=cfg.max_history_days))]
    df = df.dropna(subset=["home_pts", "away_pts"])
    if len(df) < 100:
        raise InsufficientDataError(f"Solo {len(df)} partidos con marcador; se necesitan al menos 100.")
    teams = sorted(set(df["home"]) | set(df["away"]))
    n, idx = len(teams), {t: i for i, t in enumerate(teams)}
    age = (now - df["kickoff"]).dt.total_seconds().to_numpy() / 86400.0
    w = 0.5 ** (age / cfg.half_life_days)
    hi = df["home"].map(idx).to_numpy()
    ai = df["away"].map(idx).to_numpy()
    m = len(df)
    X = np.zeros((2 * m, 2 + 2 * n))
    r = np.arange(m)
    X[r, 0], X[r, 1] = 1.0, 0.5
    X[r, 2 + hi], X[r, 2 + n + ai] = 1.0, -1.0
    X[m + r, 0], X[m + r, 1] = 1.0, -0.5
    X[m + r, 2 + ai], X[m + r, 2 + n + hi] = 1.0, -1.0
    y = np.concatenate([df["home_pts"].to_numpy(float), df["away_pts"].to_numpy(float)])
    W = np.concatenate([w, w])
    P = np.zeros(2 + 2 * n)
    P[2:] = cfg.ridge
    A = X.T @ (X * W[:, None]) + np.diag(P)
    A_inv = np.linalg.inv(A)
    theta = A_inv @ (X.T @ (W * y))

    res = y - X @ theta
    rh, ra = res[:m], res[m:]
    wsum = w.sum()
    sd_margin = float(np.sqrt((w * (rh - ra) ** 2).sum() / wsum))
    sd_total = float(np.sqrt((w * (rh + ra) ** 2).sum() / wsum))
    sd_team = float(np.sqrt((W * res**2).sum() / W.sum()))
    # Covarianza aproximada de la solución ridge con pesos (sandwich con varianza residual por punto)
    cov = A_inv @ (X.T @ (X * (W**2 * sd_team**2)[:, None])) @ A_inv
    counts = pd.concat([df["home"], df["away"]]).value_counts()
    return NbaModel(
        teams=teams, attack=theta[2:2 + n], defense=theta[2 + n:], mu=float(theta[0]), home_adv=float(theta[1]),
        sd_margin=sd_margin, sd_total=sd_total, sd_team=sd_team, cov=cov,
        games_per_team={t: float(c) for t, c in counts.items()},
        train_start=df["kickoff"].min().date().isoformat(), train_end=df["kickoff"].max().date().isoformat(),
        n_games=m, config=cfg,
    )
