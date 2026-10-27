"""Servicio NBA: ajuste del modelo de puntos, validación walk-forward y modelo para el módulo de mercados.

La validación compara, fuera de muestra, la probabilidad de victoria del modelo con:
- la frecuencia de victorias locales (baseline ingenuo), siempre;
- el mercado sin margen al cierre (moneyline), solo cuando hay cuotas guardadas de esos partidos.
Sin comparación contra el mercado el modelo queda "experimental": nunca emite candidatos de valor.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calibration.metrics import (
    accuracy,
    bootstrap_metric_diff,
    brier_score,
    expected_calibration_error,
    log_loss,
    reliability_curve,
)
from app.calibration.quality_gate import BLOCKED, EXPERIMENTAL, PRODUCTION
from app.core.config import ValueThresholds
from app.core.logging import get_logger
from app.db.models import Competition, Event, ModelVersion
from app.ingestion.store import as_utc
from app.markets import registry
from app.markets.models import MarketModel, SelectionModel
from app.markets.settlement import Outcome, settle
from app.probability.settlement import SettlementDistribution, aggregate
from app.sports.football.data import consensus_fair, matches_frame, odds_frame
from app.sports.nba import model as nm

log = get_logger(__name__)

N_SAMPLES = 200
REFIT_DAYS = 7
NBA_MARKETS = {"moneyline", "spread", "totals", "team_totals"}


# ------------------------------------------------------------------ validación
def walk_forward(games: pd.DataFrame, odds: pd.DataFrame, cfg: nm.NbaConfig, n_boot: int = 300) -> dict:
    """Predice cada partido solo con partidos anteriores (reajuste cada ``REFIT_DAYS`` días)."""
    df = games.dropna(subset=["home_pts", "away_pts"]).sort_values("kickoff").reset_index(drop=True)
    n = len(df)
    start_idx = max(600, n // 3)
    if n <= start_idx + 50:
        return {"status": "insufficient", "n_matches": n,
                "reason": f"Solo {n} partidos con marcador; se necesitan más de {start_idx + 50}."}
    market = consensus_fair(odds, "moneyline", None, True) if not odds.empty else {}
    pre = consensus_fair(odds, "moneyline", None, False) if not odds.empty else {}
    rows, oos = [], {}
    chunk = df.loc[start_idx, "kickoff"]
    end = df["kickoff"].max()
    while chunk <= end:
        nxt = chunk + pd.Timedelta(days=REFIT_DAYS)
        test = df[(df["kickoff"] >= chunk) & (df["kickoff"] < nxt)]
        if not test.empty:
            m = nm.fit(df[df["kickoff"] < chunk], chunk, cfg)
            base = float((df.loc[df["kickoff"] < chunk, "home_pts"] > df.loc[df["kickoff"] < chunk, "away_pts"])
                         .tail(1230).mean())
            for g in test.itertuples():
                if not (m.has_team(g.home) and m.has_team(g.away) and m.team_sample_ok(g.home)
                        and m.team_sample_ok(g.away)):
                    continue
                pr = m.predict(g.home, g.away)
                p = float(nm.p_home_win(pr.mu_margin, pr.sd_margin)[0])
                ev_id = int(g.event_id)
                mk = market.get(ev_id) or pre.get(ev_id) or {}
                rows.append({"event_id": ev_id, "p": p, "base": base, "m": mk.get("home"),
                             "y": int(g.home_pts > g.away_pts), "mu_margin": pr.mu_margin, "mu_total": pr.mu_total,
                             "margin": g.home_pts - g.away_pts, "total": g.home_pts + g.away_pts})
                oos[ev_id] = {"moneyline": [p, 1 - p], "mu_margin": pr.mu_margin, "mu_total": pr.mu_total}
        chunk = nxt
    res = pd.DataFrame(rows)
    out: dict = {"status": "ok", "n_matches": n, "n_eval": len(res), "refit_days": REFIT_DAYS,
                 "test_period": [df.loc[start_idx, "kickoff"].isoformat(), end.isoformat()],
                 "leakage_check": "ok: cada reajuste usa solo partidos anteriores al bloque", "_oos": oos}
    if res.empty:
        return out | {"status": "insufficient", "reason": "Ningún partido evaluable."}
    P = np.column_stack([res["p"], 1 - res["p"]])
    B = np.column_stack([res["base"], 1 - res["base"]])
    y = 1 - res["y"].to_numpy()  # índice de la clase ganadora: 0 = local, 1 = visitante
    sec: dict = {
        "model": {"brier": brier_score(P, y), "log_loss": log_loss(P, y), "accuracy": accuracy(P, y),
                  "ece": expected_calibration_error(res["p"], res["y"])},
        "baseline_frecuencias": {"brier": brier_score(B, y), "log_loss": log_loss(B, y)},
        "vs_baseline_frecuencias": bootstrap_metric_diff(P, B, y, n=n_boot),
        "reliability": [b.__dict__ for b in reliability_curve(res["p"], res["y"])],
        "reliability_note": "Curva de calibración de la probabilidad de victoria local.",
        "margin_mae": float((res["mu_margin"] - res["margin"]).abs().mean()),
        "total_mae": float((res["mu_total"] - res["total"]).abs().mean()),
    }
    mk = res.dropna(subset=["m"])
    if len(mk) >= 30:
        Pm = np.column_stack([mk["p"], 1 - mk["p"]])
        M = np.column_stack([mk["m"], 1 - mk["m"]])
        ym = 1 - mk["y"].to_numpy()
        sec["baseline_mercado"] = {"n": len(mk), "brier": brier_score(M, ym), "log_loss": log_loss(M, ym)}
        sec["model_on_market_subset"] = {"brier": brier_score(Pm, ym), "log_loss": log_loss(Pm, ym)}
        sec["vs_baseline_mercado"] = bootstrap_metric_diff(Pm, M, ym, n=n_boot)
    out["moneyline"] = sec
    out["market_reference"] = {"con_cuotas": len(mk), "sin_cuotas": len(res) - len(mk)}
    return out


@dataclass
class Gate:
    status: str
    checks: list[dict] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [c["detail"] for c in self.checks if not c["passed"]]

    def market_status(self, market: str, line: float | None) -> str:
        # Solo el ganador se valida contra el mercado; hándicap y totales siguen experimentales
        return self.status if market == "moneyline" else EXPERIMENTAL


def gate(wf: dict, th: ValueThresholds) -> Gate:
    if wf.get("status") != "ok":
        return Gate(EXPERIMENTAL, [{"market": "moneyline", "check": "muestra", "passed": False,
                                    "detail": wf.get("reason", "Validación no disponible")}])
    sec, n = wf["moneyline"], wf["n_eval"]
    checks = []

    def add(check: str, passed: bool, detail: str) -> None:
        checks.append({"market": "moneyline", "check": check, "passed": bool(passed), "detail": detail})

    add("muestra", n >= th.min_eval_sample, f"{n} predicciones fuera de muestra (mínimo {th.min_eval_sample})")
    ece = sec["model"]["ece"]
    add("calibracion", ece <= th.max_ece, f"ECE = {ece:.3f} (máximo {th.max_ece:.3f})")
    vb = sec["vs_baseline_frecuencias"]
    add("supera_baseline_frecuencias", vb["high"] < 0,
        f"Δ log loss vs frecuencia de victorias locales = {vb['diff']:+.4f} (IC90 [{vb['low']:+.4f}, {vb['high']:+.4f}])")
    vm = sec.get("vs_baseline_mercado")
    if vm and sec["baseline_mercado"]["n"] >= th.min_eval_sample:
        add("no_peor_que_mercado", vm["diff"] <= th.max_logloss_gap_vs_market,
            f"Δ log loss vs mercado sin margen = {vm['diff']:+.4f} (IC90 [{vm['low']:+.4f}, {vm['high']:+.4f}]; "
            f"{sec['baseline_mercado']['n']} partidos con cuotas)")
    else:
        have = sec["baseline_mercado"]["n"] if vm else 0
        add("no_peor_que_mercado", False,
            f"Solo {have} partidos con cuotas guardadas (mínimo {th.min_eval_sample}): aún no se puede comparar "
            "con el mercado. El programador las va juntando cada día.")
    if all(c["passed"] for c in checks):
        return Gate(PRODUCTION, checks)
    market_checked = any(c["check"] == "no_peor_que_mercado" and "Solo" not in c["detail"] for c in checks)
    if n >= th.min_eval_sample and market_checked:
        return Gate(BLOCKED, checks)
    return Gate(EXPERIMENTAL, checks)


# ------------------------------------------------------------------ modelo para mercados
def _scores(kind: str, k: int) -> Outcome:
    """Marcador ficticio con la diferencia o el total ``k`` (las reglas de liquidación solo miran eso)."""
    if kind == "margin":
        return Outcome(100 + max(k, 0), 100 + max(-k, 0))
    if kind == "home":
        return Outcome(k, 0)
    if kind == "away":
        return Outcome(0, k)
    return Outcome(k, 0, value=k)


class NbaMarketModel(MarketModel):
    sport = "nba"
    name = "NBA ratings"

    def __init__(self, pred: nm.NbaPrediction, gate: Gate | None = None, seed: int = 7) -> None:
        self.pred, self.gate = pred, gate
        rng = np.random.default_rng(seed)
        self._mu_m = rng.normal(pred.mu_margin, pred.se_margin, N_SAMPLES)
        self._mu_t = rng.normal(pred.mu_total, pred.se_total, N_SAMPLES)
        z = (self._mu_t - pred.mu_total) / 2
        self._mu_h = pred.mu_home + (self._mu_m - pred.mu_margin) / 2 + z
        self._mu_a = pred.mu_away - (self._mu_m - pred.mu_margin) / 2 + z

    def support(self, m: registry.MarketDef, period: str, is_live: bool) -> str | None:
        if m.sport != "nba":
            return "Mercado de otro deporte"
        if is_live:
            return "Cuota en vivo: la plataforma no tiene modelo en vivo."
        if period != "ft":
            return "El modelo solo cubre el partido completo (con prórroga)."
        if m.key in NBA_MARKETS:
            return None
        return "Sin modelo para este mercado de NBA (solo ganador, hándicap y totales)."

    def market_status(self, market: str, line: float | None) -> str:
        return self.gate.market_status(market, line) if self.gate else EXPERIMENTAL

    def _grid(self, m: registry.MarketDef, selection: str) -> tuple[str, np.ndarray, np.ndarray, np.ndarray]:
        p = self.pred
        if m.key in ("moneyline", "spread"):
            k, base = nm.margin_distribution(p.mu_margin, p.sd_margin)
            _, samples = nm.margin_distribution(self._mu_m, p.sd_margin)
            return "margin", k, base[0], samples
        if m.key == "totals":
            k, base = nm.total_distribution(p.mu_total, p.sd_total)
            _, samples = nm.total_distribution(self._mu_t, p.sd_total)
            return "total", k, base[0], samples
        side = selection.split("_", 1)[0]
        mu, mus = (p.mu_home, self._mu_h) if side == "home" else (p.mu_away, self._mu_a)
        k, base = nm.team_distribution(mu, p.sd_team)
        _, samples = nm.team_distribution(mus, p.sd_team)
        return side, k, base[0], samples

    def selection_model(self, m: registry.MarketDef, selection: str, line: float | None) -> SelectionModel:
        kind, k, base, samples = self._grid(m, selection)
        rule = m.settlement
        outcomes = [_scores(kind, int(v)) for v in k]

        def returns(d: float) -> tuple[list[str], np.ndarray]:
            res = [settle(rule, selection, line, d, o) for o in outcomes]
            return [r[0] for r in res], np.array([r[1] for r in res])

        names, _ = returns(2.0)
        binary = all(x in ("gana", "pierde") for x in names)
        win = np.array([x == "gana" for x in names], dtype=float)
        label = f"{rule} {selection} {'' if line is None else f'{line:+g}'}"

        def dist(d: float) -> SettlementDistribution:
            nms, rets = returns(d)
            return aggregate(zip(base, nms, rets, strict=True), label)

        return SelectionModel(dist_fn=dist, ev_samples_fn=lambda d: samples @ returns(d)[1],
                              p_samples=samples @ win if binary else None, binary=binary,
                              assumption="Diferencia y total como normales discretizadas; sin lesiones ni descansos.")


# ------------------------------------------------------------------ servicio
@dataclass
class NbaState:
    competition_id: int
    competition_key: str
    data_fp: tuple
    model: nm.NbaModel | None = None
    error: str | None = None
    walkforward: dict = field(default_factory=dict)
    gate: Gate | None = None
    model_version_id: int | None = None
    last_result_at: datetime | None = None
    oos: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.model is None:
            return "sin_modelo"
        return self.gate.status if self.gate else EXPERIMENTAL


def games_frame(s: Session, competition_id: int) -> pd.DataFrame:
    df = matches_frame(s, competition_id)
    if df.empty:
        return df
    df = df[(df["status"] == "finished") & df["home_goals"].notna()]
    return df.rename(columns={"home_goals": "home_pts", "away_goals": "away_pts"})


@dataclass
class NbaEventModel:
    model: NbaMarketModel | None
    reason: str | None
    data_issues: list[str]
    calibration_score: float
    sample_score: float
    status: str


class NbaService:
    def __init__(self) -> None:
        self._states: dict[int, NbaState] = {}
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._states.clear()

    @staticmethod
    def _fp(s: Session, comp_id: int) -> tuple:
        from app.db.models import OddsSnapshot

        row = s.execute(select(func.count(Event.id), func.max(Event.updated_at))
                        .where(Event.competition_id == comp_id, Event.status == "finished")).one()
        n_odds = s.scalar(select(func.count(OddsSnapshot.id)).join(Event)
                          .where(Event.competition_id == comp_id)) or 0
        return (int(row[0] or 0), str(row[1]), int(n_odds))

    def state(self, s: Session, comp: Competition, th: ValueThresholds, force: bool = False) -> NbaState:
        fp = self._fp(s, comp.id)
        with self._lock:
            cached = self._states.get(comp.id)
            if cached and cached.data_fp == fp and not force:
                return cached
            st = self._build(s, comp, fp, th)
            self._states[comp.id] = st
            return st

    def _build(self, s: Session, comp: Competition, fp: tuple, th: ValueThresholds) -> NbaState:
        st = NbaState(comp.id, comp.key, fp)
        df = games_frame(s, comp.id)
        if df.empty:
            st.error = "Sin partidos de NBA con marcador. Importa el historial (python -m app.cli nba-historial)."
            return st
        now = pd.Timestamp(datetime.now(UTC))
        st.last_result_at = df["kickoff"].max().to_pydatetime()
        cfg = nm.NbaConfig(min_team_games=th.min_team_matches)
        try:
            st.model = nm.fit(df, now, cfg)
        except nm.InsufficientDataError as exc:
            st.error = str(exc)
            return st
        odds = odds_frame(s, df["event_id"].astype(int).tolist())
        try:
            st.walkforward = walk_forward(df, odds, cfg)
        except Exception as exc:  # el modelo sigue disponible, sin validación
            log.exception("Walk-forward NBA falló")
            st.walkforward = {"status": "error", "reason": str(exc)}
        st.oos = st.walkforward.pop("_oos", {})
        st.gate = gate(st.walkforward, th)
        st.model_version_id = self._persist(s, st)
        return st

    @staticmethod
    def _persist(s: Session, st: NbaState) -> int:
        m = st.model
        assert m is not None
        meta = m.metadata()
        mv = s.scalar(select(ModelVersion).where(ModelVersion.name == m.name, ModelVersion.version == m.version,
                                                 ModelVersion.fingerprint == meta["fingerprint"]))
        if mv is None:
            wf = st.walkforward
            mv = ModelVersion(
                sport_key="nba", competition_key=st.competition_key, name=m.name, version=m.version,
                fingerprint=meta["fingerprint"], train_start=m.train_start, train_end=m.train_end,
                validation_period=" → ".join(wf.get("test_period", [])), features=m.features,
                params={k: v for k, v in meta.items() if k != "features"},
                calibration_metrics={"moneyline": wf["moneyline"]["model"]} if "moneyline" in wf else {},
                economic_metrics={}, status=st.status, status_reason="; ".join(st.gate.reasons) if st.gate else "",
                is_synthetic=False,
            )
            s.add(mv)
            s.commit()
        return mv.id

    def market_model(self, s: Session, ev: Event, th: ValueThresholds) -> NbaEventModel:
        st = self.state(s, ev.competition, th)
        home, away = ev.home_team.name, ev.away_team.name
        n_eval = st.walkforward.get("n_eval") or 0
        sample = float(min(1.0, n_eval / max(2 * th.min_eval_sample, 1)))
        try:
            ece = st.walkforward["moneyline"]["model"]["ece"]
            calib = float(max(0.0, 1.0 - ece / 0.10))
        except (KeyError, TypeError):
            calib = 0.0
        em = NbaEventModel(None, None, [], calib, sample, st.status)
        if st.model is None:
            em.reason = st.error or "Modelo no disponible"
            return em
        missing = [t for t in (home, away) if not st.model.has_team(t) or not st.model.team_sample_ok(t)]
        if missing:
            em.reason = f"Sin historial suficiente de {', '.join(missing)} (o nombre no normalizado)."
            return em
        if st.last_result_at and as_utc(ev.kickoff_utc) - as_utc(st.last_result_at) > timedelta(
                days=th.max_data_age_days):
            em.data_issues.append(f"Último resultado usado: {as_utc(st.last_result_at).date()}. Cambios de "
                                  "plantilla desde entonces no están reflejados.")
        em.model = NbaMarketModel(st.model.predict(home, away), st.gate, seed=ev.id)
        return em


nba_service = NbaService()
