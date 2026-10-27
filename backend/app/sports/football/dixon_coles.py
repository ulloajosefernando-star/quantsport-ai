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

Opciones (apagadas por defecto; se activan por competición cuando la validación contra el cierre mejora):
* ``team_home``: localía propia de cada equipo, encogida hacia la de la liga. Recoge la altitud (Toluca,
  Pachuca, CDMX, Puebla), los viajes largos y el ambiente de cada estadio.
* ``tournament_decay``: peso extra para los torneos anteriores (Apertura = jul-dic, Clausura = ene-jun).
  Entre torneos cortos cambian las plantillas; con 0.8, el torneo anterior pesa un 20 % menos.
* ``knockout``: ajuste de localía para eliminatorias a ida y vuelta (Liguilla), que se detectan cuando el
  mismo cruce se repite con los papeles invertidos en pocos días.
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
    team_home: bool = False
    l2_home: float = 20.0  # encogimiento de la localía propia hacia la de la liga
    tournament_decay: float = 1.0  # 1 = sin efecto
    knockout: bool = False
    l2_knockout: float = 10.0


KNOCKOUT_WINDOW_DAYS = 10


def tournament_id(ts: pd.Series) -> np.ndarray:
    """Torneo corto de cada fecha: Clausura (ene-jun) y Apertura (jul-dic) de cada año."""
    return (ts.dt.year * 2 + (ts.dt.month >= 7)).to_numpy()


def flag_two_leg(df: pd.DataFrame, window_days: int = KNOCKOUT_WINDOW_DAYS) -> np.ndarray:
    """Marca partidos cuyo cruce inverso (visitante en casa) se juega a ``window_days`` días o menos."""
    k = pd.to_datetime(df["kickoff"], utc=True)
    by_pair: dict[tuple[str, str], list[pd.Timestamp]] = {}
    for h, a, t in zip(df["home"], df["away"], k, strict=True):
        by_pair.setdefault((h, a), []).append(t)
    win = pd.Timedelta(days=window_days)
    return np.array([any(abs(t - u) <= win for u in by_pair.get((a, h), []))
                     for h, a, t in zip(df["home"], df["away"], k, strict=True)], dtype=bool)


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
    home_dev: np.ndarray | None = None
    ko_adj: float = 0.0
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

    def home_advantage(self, home: str, knockout: bool = False) -> float:
        """Localía (escala log) del equipo local en este partido."""
        v = self.home_adv + (float(self.home_dev[self._ix(home)]) if self.home_dev is not None else 0.0)
        return v + (self.ko_adj if knockout and self.config.knockout else 0.0)

    def expected_goals(self, home: str, away: str, knockout: bool = False) -> tuple[float, float]:
        th = _unpack(self.theta(), len(self.teams), self.config, self.rho_estimated)
        return _lambdas(th, self._ix(home), self._ix(away), knockout and self.config.knockout)

    def predict(self, home: str, away: str, knockout: bool = False) -> ScoreMatrix:
        lam, mu = self.expected_goals(home, away, knockout)
        return score_matrix(lam, mu, clip_rho(self.rho, lam, mu), self.config.max_goals)

    def theta(self) -> np.ndarray:
        parts = [np.array([self.mu, self.home_adv]), self.attack, self.defence]
        if self.config.team_home:
            parts.append(self.home_dev if self.home_dev is not None else np.zeros(len(self.teams)))
        if self.config.knockout:
            parts.append(np.array([self.ko_adj]))
        if self.rho_estimated:
            parts.append(np.array([self.rho]))
        return np.concatenate(parts)

    def sample_predictions(
        self, home: str, away: str, fn: Callable[[ScoreMatrix], np.ndarray], n: int = 300, seed: int = 7,
        knockout: bool = False,
    ) -> np.ndarray:
        """Propaga la incertidumbre de parámetros (Laplace) a cualquier función de la matriz."""
        if self.cov is None:
            return np.array([fn(self.predict(home, away, knockout))])
        rng = np.random.default_rng(seed)
        k = len(self.teams)
        h, a = self._ix(home), self._ix(away)
        draws = rng.multivariate_normal(self.theta(), self.cov, size=n, method="eigh")
        out = []
        for th in draws:
            p = _unpack(th, k, self.config, self.rho_estimated)
            rho = float(np.clip(p["rho"], -RHO_LIMIT, RHO_LIMIT))
            lam, mu = _lambdas(p, h, a, knockout and self.config.knockout)
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
            "team_home_advantage": ({t: round(float(np.exp(self.home_adv + self.home_dev[i])), 3)
                                     for t, i in self.index.items()} if self.home_dev is not None else None),
            "knockout_home_adjustment": round(float(np.exp(self.ko_adj)), 4) if self.config.knockout else None,
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
def _unpack(theta: np.ndarray, k: int, cfg: DixonColesConfig, fit_rho: bool) -> dict:
    """Vector de parámetros → nombres: [μ, localía, ataque(k), defensa(k), localía_propia(k)?, eliminatoria?, ρ?]."""
    i = 2 + 2 * k
    p = {"mu": theta[0], "home": theta[1], "att": theta[2:2 + k], "def": theta[2 + k:i],
         "hdev": np.zeros(k), "ko": 0.0, "rho": 0.0}
    if cfg.team_home:
        p["hdev"] = theta[i:i + k]
        i += k
    if cfg.knockout:
        p["ko"] = theta[i]
        i += 1
    if fit_rho:
        p["rho"] = theta[i]
    return p


def _lambdas(p: dict, h, a, ko) -> tuple:
    """Goles esperados de local y visitante (escalares o vectores de índices)."""
    eta_h = p["mu"] + p["home"] + p["hdev"][h] + p["ko"] * ko + p["att"][h] - p["def"][a]
    eta_a = p["mu"] + p["att"][a] - p["def"][h]
    if np.ndim(eta_h) == 0:
        return float(np.exp(eta_h)), float(np.exp(eta_a))
    return np.exp(eta_h), np.exp(eta_a)


def _loss_and_grad(
    theta: np.ndarray,
    hi: np.ndarray,
    ai: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    k: int,
    cfg: DixonColesConfig,
    fit_rho: bool,
    ko: np.ndarray,
) -> tuple[float, np.ndarray]:
    p = _unpack(theta, k, cfg, fit_rho)
    att, dfn, rho = p["att"], p["def"], p["rho"]
    lam, mu = _lambdas(p, hi, ai, ko)
    eta_h, eta_a = np.log(lam), np.log(mu)

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
    reg = 0.5 * cfg.l2 * (np.sum(att**2) + np.sum(dfn**2))
    reg += 0.5 * cfg.l2_home * np.sum(p["hdev"] ** 2) + 0.5 * cfg.l2_knockout * p["ko"] ** 2
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
    grad[2 : 2 + k] = -g_att + cfg.l2 * att
    grad[2 + k : 2 + 2 * k] = -g_def + cfg.l2 * dfn
    i = 2 + 2 * k
    if cfg.team_home:
        grad[i:i + k] = -np.bincount(hi, weights=gh, minlength=k) + cfg.l2_home * p["hdev"]
        i += k
    if cfg.knockout:
        grad[i] = -np.sum(gh * ko) + cfg.l2_knockout * p["ko"]
        i += 1
    if fit_rho:
        grad[i] = -np.sum(w * dr)
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

    ``matches`` requiere columnas: kickoff (datetime UTC), home, away, home_goals, away_goals; con
    ``knockout`` activo usa la columna opcional ``knockout`` (si falta, se calcula con ``flag_two_leg``).
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
    if cfg.tournament_decay != 1.0:
        now_t = int(tournament_id(pd.Series([as_of_ts]))[0])
        w = w * cfg.tournament_decay ** np.maximum(now_t - tournament_id(df["kickoff"]), 0)
    if cfg.knockout:
        ko = (df["knockout"].to_numpy(dtype=bool) if "knockout" in df else flag_two_leg(df)).astype(float)
    else:
        ko = np.zeros(len(df))

    fit_rho = len(df) >= cfg.min_matches_for_rho
    n_params = 2 + 2 * k + (k if cfg.team_home else 0) + (1 if cfg.knockout else 0) + (1 if fit_rho else 0)
    theta0 = np.zeros(n_params)
    theta0[0] = np.log(max(np.average(np.r_[x, y], weights=np.r_[w, w]), 0.1))
    theta0[1] = 0.2

    def fg(th: np.ndarray) -> tuple[float, np.ndarray]:
        return _loss_and_grad(th, hi, ai, x, y, w, k, cfg, fit_rho, ko)

    bounds = [(None, None)] * (n_params - (1 if fit_rho else 0)) + ([(-RHO_LIMIT, RHO_LIMIT)] if fit_rho else [])
    res = minimize(fg, theta0, jac=True, method="L-BFGS-B", bounds=bounds, options={"maxiter": 3000})
    th = res.x
    p = _unpack(th, k, cfg, fit_rho)

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
        mu=float(p["mu"]),
        home_adv=float(p["home"]),
        attack=np.array(p["att"]).copy(),
        defence=np.array(p["def"]).copy(),
        rho=float(p["rho"]),
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
        home_dev=np.array(p["hdev"]).copy() if cfg.team_home else None,
        ko_adj=float(p["ko"]),
        competition=competition,
    )
