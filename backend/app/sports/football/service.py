"""Servicio de análisis de fútbol: ajuste por competición, validación y análisis por evento."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.backtesting import football_walkforward as wf
from app.calibration import quality_gate
from app.core.config import ValueThresholds
from app.core.logging import get_logger
from app.db.models import BacktestRun, CalibrationReport, Competition, Event, ModelVersion
from app.ingestion.store import as_utc
from app.probability.margin import compare_methods
from app.probability.settlement import binary_distribution
from app.providers import clubelo
from app.sports.football import dixon_coles as dc
from app.sports.football.data import consensus_fair, finished, matches_frame, odds_frame
from app.sports.football.scores import (
    ScoreMatrix,
    handicap_distribution,
    probs_1x2,
    probs_btts,
    probs_totals,
    summary,
    totals_distribution,
)
from app.value_engine.engine import CATEGORY_ORDER, Offer, SelectionInput, assess

log = get_logger(__name__)

N_SAMPLES = 300
MAX_HISTORY_DAYS = 4 * 365

SELECTION_LABELS = {
    ("1x2", "home"): "Local", ("1x2", "draw"): "Empate", ("1x2", "away"): "Visitante",
    ("btts", "yes"): "Ambos anotan: Sí", ("btts", "no"): "Ambos anotan: No",
}

LIMITATIONS_BASE = [
    "El modelo solo usa goles, localía y fecha; no incorpora alineaciones, lesiones ni xG (no verificados en Fase 1).",
    "Las probabilidades se estiman con error; el intervalo refleja solo la incertidumbre de parámetros, "
    "no cambios de plantilla, motivación o calendario.",
    "Eliminar el margen da una estimación del consenso del mercado, no la probabilidad verdadera.",
]


@dataclass
class CompetitionState:
    competition_id: int
    competition_key: str
    competition_name: str
    is_synthetic: bool
    data_fp: tuple
    model: dc.DixonColesModel | None = None
    error: str | None = None
    walkforward: dict = field(default_factory=dict)
    gate: quality_gate.GateResult | None = None
    model_version_id: int | None = None
    last_result_at: datetime | None = None

    @property
    def status(self) -> str:
        if self.model is None:
            return "sin_modelo"
        return self.gate.status if self.gate else quality_gate.EXPERIMENTAL

    @property
    def calibration_score(self) -> float:
        try:
            ece = self.walkforward["1x2"]["model"]["ece"]
        except (KeyError, TypeError):
            return 0.0
        return float(max(0.0, 1.0 - ece / 0.10))


class FootballService:
    def __init__(self) -> None:
        self._states: dict[int, CompetitionState] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------- competition
    @staticmethod
    def _data_fingerprint(s: Session, comp_id: int) -> tuple:
        row = s.execute(
            select(func.count(Event.id), func.max(Event.updated_at),
                   func.sum(func.coalesce(Event.home_score, 0) + func.coalesce(Event.away_score, 0)))
            .where(Event.competition_id == comp_id, Event.status == "finished")
        ).one()
        return (int(row[0] or 0), str(row[1]), int(row[2] or 0))

    def state(self, s: Session, comp: Competition, th: ValueThresholds, force: bool = False) -> CompetitionState:
        fp = self._data_fingerprint(s, comp.id)
        with self._lock:
            cached = self._states.get(comp.id)
            if cached and cached.data_fp == fp and not force:
                return cached
            st = self._build_state(s, comp, fp, th)
            self._states[comp.id] = st
            return st

    def invalidate(self) -> None:
        with self._lock:
            self._states.clear()

    def _build_state(self, s: Session, comp: Competition, fp: tuple, th: ValueThresholds) -> CompetitionState:
        st = CompetitionState(comp.id, comp.key, comp.name, comp.is_synthetic, fp)
        df = finished(matches_frame(s, comp.id))
        now = pd.Timestamp(datetime.now(UTC))
        if df.empty:
            st.error = "Sin partidos finalizados."
            return st
        df = df[df["kickoff"] >= now - pd.Timedelta(days=MAX_HISTORY_DAYS)]
        st.last_result_at = df["kickoff"].max().to_pydatetime() if not df.empty else None
        cfg = dc.DixonColesConfig(min_team_matches=th.min_team_matches)
        try:
            st.model = dc.fit(df, now, cfg, competition=comp.key)
        except dc.InsufficientDataError as exc:
            st.error = str(exc)
            return st
        odds = odds_frame(s, df["event_id"].astype(int).tolist())
        try:
            st.walkforward = wf.run(df, odds, cfg, wf.WalkForwardConfig(min_ev=th.min_ev, min_odds=th.min_odds,
                                                                        max_odds=th.max_odds))
        except Exception as exc:  # el análisis sigue disponible, pero sin validación
            log.exception("Walk-forward falló para %s", comp.key)
            st.walkforward = {"status": "error", "reason": str(exc)}
        st.gate = quality_gate.evaluate(st.walkforward, th)
        st.model_version_id = self._persist_model(s, st)
        return st

    @staticmethod
    def _persist_model(s: Session, st: CompetitionState) -> int:
        m = st.model
        assert m is not None
        meta = m.metadata()
        mv = s.scalar(select(ModelVersion).where(ModelVersion.name == m.name, ModelVersion.version == m.version,
                                                 ModelVersion.fingerprint == meta["fingerprint"]))
        wfr = st.walkforward
        calib = {k: wfr[k]["model"] for k in ("1x2", "totals_2.5", "btts") if k in wfr}
        calib["market_status"] = {k: v["status"] for k, v in (st.gate.markets if st.gate else {}).items()}
        if mv is None:
            mv = ModelVersion(
                sport_key="football", competition_key=st.competition_key, name=m.name, version=m.version,
                fingerprint=meta["fingerprint"], train_start=m.train_start, train_end=m.train_end,
                validation_period=" → ".join(wfr.get("test_period", [])), features=m.features,
                params={k: v for k, v in meta.items() if k not in ("features",)},
                calibration_metrics=calib, economic_metrics=wfr.get("betting", {}),
                status=st.gate.status if st.gate else "experimental",
                status_reason="; ".join(st.gate.reasons) if st.gate else "", is_synthetic=st.is_synthetic,
            )
            s.add(mv)
            s.flush()
            for market in ("1x2", "totals_2.5", "btts"):
                if market in wfr:
                    rel = wfr[market].get("reliability") or []
                    s.add(CalibrationReport(model_version_id=mv.id, market=market, n=wfr["n_eval"],
                                            metrics=wfr[market]["model"], reliability=rel))
            s.add(BacktestRun(sport_key="football", competition_key=st.competition_key, model_name=m.name,
                              config={"refit_days": wfr.get("refit_days")}, results=_jsonable(wfr),
                              odds_kind=wfr.get("odds_kind", "none"), is_synthetic=st.is_synthetic))
            s.commit()
        return mv.id

    # -------------------------------------------------------------- event
    def analyze_event(self, s: Session, ev: Event, th: ValueThresholds, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        comp = ev.competition
        st = self.state(s, comp, th)
        home, away = ev.home_team.name, ev.away_team.name
        kickoff = as_utc(ev.kickoff_utc)
        out: dict = {
            "event_id": ev.id, "sport": "football", "competition": comp.name, "competition_key": comp.key,
            "home": home, "away": away, "kickoff_utc": kickoff.isoformat(), "status": ev.status,
            "is_synthetic": ev.is_synthetic or comp.is_synthetic, "source": ev.source,
            "model": None, "model_status": st.status, "probabilities": None, "assessments": [],
            "data_quality": {}, "limitations": list(LIMITATIONS_BASE), "factors": [],
        }
        if out["is_synthetic"]:
            out["limitations"].insert(0, "DATOS SINTÉTICOS de demostración: no representan partidos ni cuotas reales.")
        issues: list[str] = []
        if st.model is None:
            issues.append(st.error or "Modelo no disponible")
        else:
            m = st.model
            out["model"] = m.metadata() | {
                "status": st.status, "gate": st.gate.checks if st.gate else [],
                "market_status": {k: v["status"] for k, v in (st.gate.markets if st.gate else {}).items()},
            }
            for t in (home, away):
                if not m.has_team(t):
                    issues.append(f"{t}: sin historial en esta competición (p. ej. ascendido o nombre no normalizado).")
                elif not m.team_sample_ok(t):
                    issues.append(f"{t}: solo {m.team_matches.get(t, 0)} partidos (mínimo {th.min_team_matches}).")
            if st.last_result_at and (kickoff - st.last_result_at) > timedelta(days=th.max_data_age_days):
                issues.append(
                    f"Último resultado usado: {st.last_result_at.date()} (más de {th.max_data_age_days} días "
                    "antes del partido). Posibles cambios de plantilla no reflejados."
                )
        out["external_ratings"] = _external_ratings(s, ev, kickoff)
        out["data_quality"] = {
            "last_result_used": st.last_result_at.isoformat() if st.last_result_at else None,
            "issues": issues,
            "event_fetched_at": as_utc(ev.fetched_at).isoformat() if ev.fetched_at else None,
        }

        odds = odds_frame(s, [ev.id])
        live = odds[~odds["is_closing"]] if not odds.empty else odds
        out["odds_history"] = _odds_history(live)
        out["odds_table"] = _odds_table(live)

        if st.model is None or any("sin historial" in i or "solo" in i for i in issues):
            out["assessments"] = []
            out["summary_category"] = "datos_insuficientes"
            return out

        m = st.model
        sm = m.predict(home, away)
        out["probabilities"] = summary(sm)
        sampled = list(m.sample_predictions(home, away, lambda x: x, n=N_SAMPLES))
        p_samples = {
            ("1x2", "home"): np.array([probs_1x2(x)["home"] for x in sampled]),
            ("1x2", "draw"): np.array([probs_1x2(x)["draw"] for x in sampled]),
            ("1x2", "away"): np.array([probs_1x2(x)["away"] for x in sampled]),
            ("btts", "yes"): np.array([probs_btts(x)["yes"] for x in sampled]),
        }
        p_samples[("btts", "no")] = 1 - p_samples[("btts", "yes")]
        out["probabilities"]["intervals_90"] = {
            f"{k[0]}:{k[1]}": [float(np.quantile(v, 0.05)), float(np.quantile(v, 0.95))] for k, v in p_samples.items()
        }
        lam_s = np.array([x.lambda_home for x in sampled])
        mu_s = np.array([x.lambda_away for x in sampled])
        out["probabilities"]["xg_intervals_90"] = {
            "home": [float(np.quantile(lam_s, 0.05)), float(np.quantile(lam_s, 0.95))],
            "away": [float(np.quantile(mu_s, 0.05)), float(np.quantile(mu_s, 0.95))],
        }
        out["factors"] = _factors(m, home, away, sm)
        ext = out["external_ratings"]
        if ext:
            p1 = out["probabilities"]["1x2"]
            ext["model_expectancy"] = float(p1["home"] + 0.5 * p1["draw"])
            ext["gap"] = ext["model_expectancy"] - ext["expectancy_neutral"]
            ext["discrepancy"] = abs(ext["gap"]) > ELO_DISCREPANCY

        selections = self._selections(sm, sampled, p_samples, live, kickoff)
        data_issues = [i for i in issues if "Último resultado" in i]
        assessments = []
        for sel, ps in selections:
            status = st.gate.market_status(sel.market, sel.line) if st.gate else "experimental"
            a = assess(sel, th, status, data_issues, st.calibration_score, now=now, p_samples=ps)
            a.is_synthetic = a.is_synthetic or out["is_synthetic"]
            assessments.append(a.to_dict())
        out["assessments"] = assessments
        out["margin_analysis"] = _margin_analysis(live)
        cats = [a["category"] for a in assessments if a["n_offers"]]
        out["summary_category"] = min(cats, key=CATEGORY_ORDER.index) if cats else "datos_insuficientes"
        return out

    @staticmethod
    def _selections(sm: ScoreMatrix, sampled: list[ScoreMatrix], p_samples: dict, live: pd.DataFrame,
                    kickoff: datetime) -> list[tuple[SelectionInput, np.ndarray | None]]:
        out: list[tuple[SelectionInput, np.ndarray | None]] = []
        latest = (live.sort_values("captured_at").groupby(["bookmaker", "market", "line", "selection"], dropna=False)
                  .tail(1)) if not live.empty else live

        def offers(market: str, sel: str, line: float | None) -> list[Offer]:
            if latest.empty:
                return []
            sub = latest[(latest["market"] == market) & (latest["selection"] == sel)]
            sub = sub[sub["line"].isna()] if line is None else sub[np.isclose(sub["line"].astype(float), line)]
            sub = sub[sub["bookmaker"] != "Máximo mercado"]
            return [Offer(r.bookmaker, float(r.odds), r.captured_at.to_pydatetime(), r.source, bool(r.is_synthetic))
                    for r in sub.itertuples()]

        fair_1x2 = consensus_fair(live, "1x2", None, False) if not live.empty else {}
        fair_btts = consensus_fair(live, "btts", None, False) if not live.empty else {}
        f1 = next(iter(fair_1x2.values()), {}) if fair_1x2 else {}
        fb = next(iter(fair_btts.values()), {}) if fair_btts else {}

        for sel in ("home", "draw", "away"):
            p = probs_1x2(sm)[sel]
            ps = p_samples[("1x2", sel)]
            out.append((SelectionInput(
                market="1x2", selection=sel, line=None, label=SELECTION_LABELS[("1x2", sel)],
                offers=offers("1x2", sel, None), p_market_fair=f1.get(sel),
                dist_fn=lambda d, p=p: binary_distribution(p, d),
                ev_samples_fn=lambda d, ps=ps: ps * d - 1,
            ), ps))
        for sel in ("yes", "no"):
            p = probs_btts(sm)[sel]
            ps = p_samples[("btts", sel)]
            out.append((SelectionInput(
                market="btts", selection=sel, line=None, label=SELECTION_LABELS[("btts", sel)],
                offers=offers("btts", sel, None), p_market_fair=fb.get(sel),
                dist_fn=lambda d, p=p: binary_distribution(p, d),
                ev_samples_fn=lambda d, ps=ps: ps * d - 1,
            ), ps))

        lines_tot = sorted({float(x) for x in latest[latest["market"] == "totals"]["line"].dropna()}) \
            if not latest.empty else []
        for line in lines_tot or [2.5]:
            fair = consensus_fair(live, "totals", line, False) if not live.empty else {}
            ft = next(iter(fair.values()), {}) if fair else {}
            half_line = abs((line * 2) % 2 - 1) < 1e-9  # x.5 => sin push
            for sel in ("over", "under"):
                label = f"{'Más' if sel == 'over' else 'Menos'} de {line:g} goles"
                if half_line:
                    p = probs_totals(sm, line)[sel]
                    ps = np.array([probs_totals(x, line)[sel] for x in sampled])
                    out.append((SelectionInput(
                        market="totals", selection=sel, line=line, label=label, offers=offers("totals", sel, line),
                        p_market_fair=ft.get(sel), dist_fn=lambda d, p=p: binary_distribution(p, d),
                        ev_samples_fn=lambda d, ps=ps: ps * d - 1,
                    ), ps))
                else:
                    out.append((SelectionInput(
                        market="totals", selection=sel, line=line, label=label, offers=offers("totals", sel, line),
                        p_market_fair=None, binary=False,
                        dist_fn=lambda d, sel=sel, line=line: totals_distribution(sm, sel, line, d),
                        ev_samples_fn=lambda d, sel=sel, line=line: np.array(
                            [totals_distribution(x, sel, line, d).expected_value for x in sampled]),
                    ), None))

        if not latest.empty:
            ah = latest[latest["market"] == "ah"][["selection", "line"]].drop_duplicates()
            for r in ah.itertuples():
                side, line = r.selection, float(r.line)
                team = "Local" if side == "home" else "Visitante"
                out.append((SelectionInput(
                    market="ah", selection=side, line=line, label=f"Hándicap asiático {team} {line:+g}",
                    offers=offers("ah", side, line), p_market_fair=None, binary=False,
                    dist_fn=lambda d, side=side, line=line: handicap_distribution(sm, side, line, d),
                    ev_samples_fn=lambda d, side=side, line=line: np.array(
                        [handicap_distribution(x, side, line, d).expected_value for x in sampled]),
                ), None))
        return out


# ------------------------------------------------------------------ helpers
ELO_DISCREPANCY = 0.20  # el modelo incluye localía y Elo neutral no: ~0.05-0.10 de diferencia es normal


def _external_ratings(s: Session, ev: Event, kickoff: datetime) -> dict | None:
    """Club Elo como referencia externa (no entra en el modelo ni en la categoría de valor)."""
    h, a = clubelo.rating_at(s, ev.home_team_id, kickoff), clubelo.rating_at(s, ev.away_team_id, kickoff)
    if h is None or a is None:
        return None
    diff = h - a
    return {"source": "Club Elo", "home": h, "away": a, "diff": diff,
            "expectancy_neutral": 1.0 / (1.0 + 10 ** (-diff / 400.0)),
            "note": "Expectativa Elo en campo neutral (victoria = 1, empate = 0.5). Referencia externa: "
                    "no se usa para calcular probabilidades ni oportunidades."}


def _factors(m: dc.DixonColesModel, home: str, away: str, sm: ScoreMatrix) -> list[dict]:
    ih, ia = m.index[home], m.index[away]
    return [
        {"factor": "Goles esperados", "home": round(sm.lambda_home, 2), "away": round(sm.lambda_away, 2),
         "detail": "Media de goles del modelo para cada equipo en este partido."},
        {"factor": "Fuerza ofensiva (× media liga)", "home": round(float(np.exp(m.attack[ih])), 2),
         "away": round(float(np.exp(m.attack[ia])), 2), "detail": "1.00 = media de la competición."},
        {"factor": "Solidez defensiva (× media liga)", "home": round(float(np.exp(m.defence[ih])), 2),
         "away": round(float(np.exp(m.defence[ia])), 2), "detail": ">1 concede menos que la media."},
        {"factor": "Ventaja de localía", "home": round(float(np.exp(m.home_adv)), 2), "away": None,
         "detail": "Multiplicador de goles esperado del local estimado en la competición."},
        {"factor": "Partidos en la muestra", "home": m.team_matches.get(home, 0), "away": m.team_matches.get(away, 0),
         "detail": "Partidos usados (ponderados por antigüedad)."},
    ]


def _odds_table(live: pd.DataFrame) -> list[dict]:
    if live.empty:
        return []
    latest = live.sort_values("captured_at").groupby(["bookmaker", "market", "line", "selection"], dropna=False).tail(1)
    rows = []
    for r in latest.itertuples():
        rows.append({"bookmaker": r.bookmaker, "market": r.market, "line": None if pd.isna(r.line) else float(r.line),
                     "selection": r.selection, "odds": float(r.odds), "captured_at": r.captured_at.isoformat(),
                     "source": r.source, "is_synthetic": bool(r.is_synthetic)})
    return rows


def _odds_history(live: pd.DataFrame) -> list[dict]:
    if live.empty:
        return []
    sub = live[live["market"] == "1x2"].sort_values("captured_at")
    return [{"captured_at": r.captured_at.isoformat(), "bookmaker": r.bookmaker, "selection": r.selection,
             "odds": float(r.odds)} for r in sub.itertuples()]


def _margin_analysis(live: pd.DataFrame) -> list[dict]:
    if live.empty:
        return []
    out = []
    latest = live.sort_values("captured_at").groupby(["bookmaker", "market", "line", "selection"], dropna=False).tail(1)
    for bm, g in latest[latest["market"] == "1x2"].groupby("bookmaker"):
        m = dict(zip(g["selection"], g["odds"], strict=True))
        if all(k in m for k in ("home", "draw", "away")):
            cmp = compare_methods([m["home"], m["draw"], m["away"]])
            out.append({"bookmaker": bm, "overround": cmp.overround,
                        "methods": {k: dict(zip(("home", "draw", "away"), v, strict=True))
                                    for k, v in cmp.by_method.items()},
                        "max_disagreement": cmp.max_disagreement})
    return out


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.floating | np.integer):
        return obj.item()
    if isinstance(obj, float) and obj != obj:
        return None
    return obj


football_service = FootballService()
