"""Modelo de goles Poisson con extensión Dixon-Coles (1997).

    log λ_local     = μ + localía + ataque[local]     - defensa[visitante]
    log λ_visitante = μ +           ataque[visitante] - defensa[local]
    P(x, y) = τ(x, y; λ, λ', ρ) · Pois(x; λ) · Pois(y; λ')

Decisiones de diseño (documentadas, configurables):
* Ponderación temporal w = exp(-ξ · días_de_antigüedad); ξ por defecto ≈ semivida de 1 año.
* Regularización L2 (ridge) sobre ataque y defensa: evita fuerzas extremas con poca muestra
  y resuelve la identificabilidad (los parámetros se encogen hacia la media de la liga).
* ρ solo se estima si hay suficientes partidos; si no, ρ = 0 (Poisson independiente) y se
  declara en los metadatos.
* Se ajusta por competición (y ventana temporal); no se mezclan ligas.
* Incertidumbre de parámetros: aproximación de Laplace (inversa del Hessiano numérico). Con
  verosimilitud ponderada es una aproximación; se documenta como tal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

from app.sports.football.scores import ScoreMatrix, rho_bounds, score_matrix

MODEL_NAME = "football_dixon_coles"
MODEL_VERSION = "1.0.0"
RHO_LIMIT = 0.25


def clip_rho(rho: float, lam: float, mu: float) -> float:
    """Recorta ρ al rango que mantiene τ > 0 para estos λ (necesario con λ extremos)."""
    lo, hi = rho_bounds(lam, mu)
    return float(min(max(rho, lo + 1e-6), hi - 1e-6))


class InsufficientDataError(ValueError):
    pass


class UnknownTeamError(KeyError):
    pass


@dataclass
class DixonColesConfig:
    xi_per_day: float = 0.0019  # semivida ≈ 365 días
    l2: float = 5.0  # precisión del prior normal sobre ataque/defensa
    min_matches: int = 120
    min_matches_for_rho: int = 300
    min_team_matches: int = 8
    max_goals: int = 10


@dataclass
class DixonColesModel:
    teams: list[str]
    mu: float
    home_adv: float
    attack: np.ndarray
    defence: np.ndarray
    rho: float
    rho_estimated: bool
    cov: np.ndarray | None
    team_matches: dict[str, int]
    n_matches: int
    effective_n: float
    train_start: str
    train_end: str
    as_of: str
    loglik: float
    converged: bool
    config: DixonColesConfig
    competition: str = ""
    name: str = MODEL_NAME
    version: str = MODEL_VERSION
    features: list[str] = field(
        default_factory=lambda: ["goles_local", "goles_visitante", "localía", "fecha (ponderación temporal)"]
    )

    # ------------------------------------------------------------------ helpers
    @property
    def index(self) -> dict[str, int]:
        return {t: i for i, t in enumerate(self.teams)}

    def _ix(self, team: str) -> int:
        try:
            return self.index[team]
        except KeyError as exc:
            raise UnknownTeamError(team) from exc

    def has_team(self, team: str) -> bool:
        return team in self.index

    def team_sample_ok(self, team: str) -> bool:
        return self.team_matches.get(team, 0) >= self.config.min_team_matches

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        h, a = self._ix(home), self._ix(away)
        lam = float(np.exp(self.mu + self.home_adv + self.attack[h] - self.defence[a]))
        mu = float(np.exp(self.mu + self.attack[a] - self.defence[h]))
        return lam, mu

    def predict(self, home: str, away: str) -> ScoreMatrix:
        lam, mu = self.expected_goals(home, away)
        return score_matrix(lam, mu, clip_rho(self.rho, lam, mu), self.config.max_goals)

    def theta(self) -> np.ndarray:
        parts = [np.array([self.mu, self.home_adv]), self.attack, self.defence]
        if self.rho_estimated:
            parts.append(np.array([self.rho]))
        return np.concatenate(parts)

    def sample_predictions(
        self, home: str, away: str, fn: Callable[[ScoreMatrix], np.ndarray], n: int = 300, seed: int = 7
    ) -> np.ndarray:
        """Propaga la incertidumbre de parámetros (Laplace) a cualquier función de la matriz."""
        if self.cov is None:
            return np.array([fn(self.predict(home, away))])
        rng = np.random.default_rng(seed)
        k = len(self.teams)
        h, a = self._ix(home), self._ix(away)
        draws = rng.multivariate_normal(self.theta(), self.cov, size=n, method="eigh")
        out = []
        for th in draws:
            mu0, home_adv = th[0], th[1]
            att, dfn = th[2 : 2 + k], th[2 + k : 2 + 2 * k]
            rho = float(np.clip(th[-1], -RHO_LIMIT, RHO_LIMIT)) if self.rho_estimated else 0.0
            lam = float(np.exp(mu0 + home_adv + att[h] - dfn[a]))
            mu = float(np.exp(mu0 + att[a] - dfn[h]))
            out.append(fn(score_matrix(lam, mu, clip_rho(rho, lam, mu), self.config.max_goals)))
        return np.array(out)

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "teams": self.teams,
                "theta": np.round(self.theta(), 8).tolist(),
                "train_end": self.train_end,
                "competition": self.competition,
            }
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def metadata(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "fingerprint": self.fingerprint(),
            "competition": self.competition,
            "trained_at": self.as_of,
            "train_period": [self.train_start, self.train_end],
            "n_matches": self.n_matches,
            "effective_n": round(self.effective_n, 1),
            "rho": round(self.rho, 4),
            "rho_estimated": self.rho_estimated,
            "home_advantage": round(float(np.exp(self.home_adv)), 4),
            "converged": self.converged,
            "features": self.features,
            "config": self.config.__dict__,
            "parameter_uncertainty": "Laplace (Hessiano numérico)" if self.cov is not None else "no disponible",
        }

    def ratings(self) -> list[dict]:
        rows = []
        for t, i in self.index.items():
            rows.append(
                {
                    "team": t,
                    "attack": round(float(self.attack[i]), 4),
                    "defence": round(float(self.defence[i]), 4),
                    "matches": self.team_matches.get(t, 0),
                }
            )
        return sorted(rows, key=lambda r: r["attack"] + r["defence"], reverse=True)


# ---------------------------------------------------------------------- fitting
def _loss_and_grad(
    theta: np.ndarray,
    hi: np.ndarray,
    ai: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    k: int,
    l2: float,
    fit_rho: bool,
) -> tuple[float, np.ndarray]:
    mu0, home = theta[0], theta[1]
    att, dfn = theta[2 : 2 + k], theta[2 + k : 2 + 2 * k]
    rho = theta[-1] if fit_rho else 0.0

    eta_h = mu0 + home + att[hi] - dfn[ai]
    eta_a = mu0 + att[ai] - dfn[hi]
    lam, mu = np.exp(eta_h), np.exp(eta_a)

    tau = np.ones_like(lam)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    tau[m00] = 1 - lam[m00] * mu[m00] * rho
    tau[m01] = 1 + lam[m01] * rho
    tau[m10] = 1 + mu[m10] * rho
    tau[m11] = 1 - rho
    tau = np.maximum(tau, 1e-10)

    ll = np.log(tau) + x * eta_h - lam - gammaln(x + 1) + y * eta_a - mu - gammaln(y + 1)
    reg = 0.5 * l2 * (np.sum(att**2) + np.sum(dfn**2))
    loss = -np.sum(w * ll) + reg

    # derivadas de log τ
    dh = np.zeros_like(lam)
    da = np.zeros_like(lam)
    dr = np.zeros_like(lam)
    dh[m00] = -lam[m00] * mu[m00] * rho / tau[m00]
    da[m00] = -lam[m00] * mu[m00] * rho / tau[m00]
    dr[m00] = -lam[m00] * mu[m00] / tau[m00]
    dh[m01] = lam[m01] * rho / tau[m01]
    dr[m01] = lam[m01] / tau[m01]
    da[m10] = mu[m10] * rho / tau[m10]
    dr[m10] = mu[m10] / tau[m10]
    dr[m11] = -1.0 / tau[m11]

    gh = w * (x - lam + dh)
    ga = w * (y - mu + da)

    grad = np.zeros_like(theta)
    grad[0] = -(gh.sum() + ga.sum())
    grad[1] = -gh.sum()
    g_att = np.bincount(hi, weights=gh, minlength=k) + np.bincount(ai, weights=ga, minlength=k)
    g_def = -np.bincount(ai, weights=gh, minlength=k) - np.bincount(hi, weights=ga, minlength=k)
    grad[2 : 2 + k] = -g_att + l2 * att
    grad[2 + k : 2 + 2 * k] = -g_def + l2 * dfn
    if fit_rho:
        grad[-1] = -np.sum(w * dr)
    return float(loss), grad


def _numerical_hessian(fun_grad: Callable[[np.ndarray], tuple[float, np.ndarray]], theta: np.ndarray) -> np.ndarray:
    n = len(theta)
    hess = np.zeros((n, n))
    for i in range(n):
        eps = 1e-5 * max(1.0, abs(theta[i]))
        tp, tm = theta.copy(), theta.copy()
        tp[i] += eps
        tm[i] -= eps
        hess[:, i] = (fun_grad(tp)[1] - fun_grad(tm)[1]) / (2 * eps)
    return 0.5 * (hess + hess.T)


def fit(
    matches: pd.DataFrame,
    as_of: datetime | pd.Timestamp,
    config: DixonColesConfig | None = None,
    competition: str = "",
    compute_uncertainty: bool = True,
) -> DixonColesModel:
    """Ajusta el modelo con partidos ANTERIORES a ``as_of`` (prevención de leakage).

    ``matches`` requiere columnas: kickoff (datetime UTC), home, away, home_goals, away_goals.
    """
    cfg = config or DixonColesConfig()
    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.tz_localize("UTC")
    df = matches.dropna(subset=["home_goals", "away_goals"]).copy()
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    df = df[df["kickoff"] < as_of_ts].sort_values("kickoff")
    if len(df) < cfg.min_matches:
        raise InsufficientDataError(
            f"Se requieren al menos {cfg.min_matches} partidos finalizados; hay {len(df)}"
        )

    teams = sorted(set(df["home"]) | set(df["away"]))
    idx = {t: i for i, t in enumerate(teams)}
    k = len(teams)
    hi = df["home"].map(idx).to_numpy()
    ai = df["away"].map(idx).to_numpy()
    x = df["home_goals"].to_numpy(dtype=float)
    y = df["away_goals"].to_numpy(dtype=float)
    age_days = (as_of_ts - df["kickoff"]).dt.total_seconds().to_numpy() / 86400.0
    w = np.exp(-cfg.xi_per_day * age_days)

    fit_rho = len(df) >= cfg.min_matches_for_rho
    n_params = 2 + 2 * k + (1 if fit_rho else 0)
    theta0 = np.zeros(n_params)
    theta0[0] = np.log(max(np.average(np.r_[x, y], weights=np.r_[w, w]), 0.1))
    theta0[1] = 0.2

    def fg(th: np.ndarray) -> tuple[float, np.ndarray]:
        return _loss_and_grad(th, hi, ai, x, y, w, k, cfg.l2, fit_rho)

    bounds = [(None, None)] * (2 + 2 * k) + ([(-RHO_LIMIT, RHO_LIMIT)] if fit_rho else [])
    res = minimize(fg, theta0, jac=True, method="L-BFGS-B", bounds=bounds, options={"maxiter": 2000})
    th = res.x

    cov = None
    if compute_uncertainty:
        hess = _numerical_hessian(fg, th)
        try:
            cov = np.linalg.inv(hess)
            cov = 0.5 * (cov + cov.T)
            if not np.all(np.isfinite(cov)) or np.any(np.diag(cov) <= 0):
                cov = None
        except np.linalg.LinAlgError:
            cov = None

    counts = pd.concat([df["home"], df["away"]]).value_counts().to_dict()
    return DixonColesModel(
        teams=teams,
        mu=float(th[0]),
        home_adv=float(th[1]),
        attack=th[2 : 2 + k].copy(),
        defence=th[2 + k : 2 + 2 * k].copy(),
        rho=float(th[-1]) if fit_rho else 0.0,
        rho_estimated=fit_rho,
        cov=cov,
        team_matches={str(t): int(c) for t, c in counts.items()},
        n_matches=len(df),
        effective_n=float(w.sum() ** 2 / np.sum(w**2)),
        train_start=df["kickoff"].min().isoformat(),
        train_end=df["kickoff"].max().isoformat(),
        as_of=as_of_ts.isoformat(),
        loglik=float(-res.fun),
        converged=bool(res.success),
        config=cfg,
        competition=competition,
    )
