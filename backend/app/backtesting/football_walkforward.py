"""Backtest walk-forward para el modelo de goles de fútbol (1X2, Over/Under 2.5 y BTTS).

Protocolo:
1. Se ordenan los partidos por fecha. El primer bloque de test empieza cuando hay suficiente
   historia (``min_train``) y como mínimo tras el primer tercio de la muestra.
2. Para cada bloque de ``refit_days`` días se reentrena SOLO con partidos anteriores al inicio
   del bloque y se predicen los partidos del bloque (sin divisiones aleatorias).
3. Se comparan las probabilidades con dos baselines: frecuencias de la liga en entrenamiento y
   probabilidades de mercado sin margen (si hay cuotas).
4. Simulación económica (solo si hay cuotas): apuesta plana de 1 unidad cuando EV ≥ umbral,
   usando la cuota MEDIA pre-cierre (no la mejor, para no sobreestimar). Con stakes planos,
   ROI (beneficio / capital apostado) y yield coinciden. Si las cuotas son sintéticas, el
   resultado económico se etiqueta como hipotético.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.calibration.metrics import (
    accuracy,
    bootstrap_metric_diff,
    brier_score,
    expected_calibration_error,
    log_loss,
    multiclass_ece,
    reliability_curve,
    roc_auc,
)
from app.sports.football import adjust
from app.sports.football import dixon_coles as dc
from app.sports.football.data import average_odds, consensus_fair


@dataclass
class WalkForwardConfig:
    refit_days: int = 28
    min_train: int = 150
    min_ev: float = 0.03
    min_odds: float = 1.3
    max_odds: float = 6.0
    bootstrap: int = 400
    seed: int = 11
    draw_adjust: bool = False
    market_blend: bool = False


class LeakageError(AssertionError):
    pass


def _max_drawdown(profits: np.ndarray) -> float:
    if profits.size == 0:
        return 0.0
    curve = np.cumsum(profits)
    peak = np.maximum.accumulate(np.r_[0.0, curve])[1:]
    return float(np.max(peak - curve))


def _longest_losing_streak(profits: np.ndarray) -> int:
    best = cur = 0
    for p in profits:
        cur = cur + 1 if p < 0 else 0
        best = max(best, cur)
    return best


def betting_summary(bets: pd.DataFrame, n_boot: int = 400, seed: int = 11) -> dict:
    if bets.empty:
        return {"n_bets": 0}
    profit = bets["profit"].to_numpy()
    staked = float(len(bets))
    rng = np.random.default_rng(seed)
    boots = [profit[rng.integers(0, len(profit), len(profit))].sum() / staked for _ in range(n_boot)]
    out = {
        "n_bets": len(bets),
        "wins": int((profit > 0).sum()),
        "losses": int((profit < 0).sum()),
        "staked_units": staked,
        "profit_units": float(profit.sum()),
        "roi": float(profit.sum() / staked),
        "roi_ci90": [float(np.quantile(boots, 0.05)), float(np.quantile(boots, 0.95))],
        "max_drawdown_units": _max_drawdown(profit),
        "longest_losing_streak": _longest_losing_streak(profit),
        "avg_odds": float(bets["odds"].mean()),
    }
    if "clv" in bets and bets["clv"].notna().any():
        out["clv_mean"] = float(bets["clv"].dropna().mean())
        out["clv_n"] = int(bets["clv"].notna().sum())
    by_market = {}
    for mk, g in bets.groupby("market"):
        by_market[mk] = {"n": len(g), "roi": float(g["profit"].sum() / len(g))}
    out["by_market"] = by_market
    bins = [1.0, 1.8, 2.5, 4.0, 100.0]
    labels = ["<1.80", "1.80-2.50", "2.50-4.00", ">4.00"]
    cats = pd.cut(bets["odds"], bins=bins, labels=labels)
    out["by_odds_range"] = {
        str(k): {"n": len(g), "roi": float(g["profit"].sum() / len(g))}
        for k, g in bets.groupby(cats, observed=True)
    }
    return out


def _binary_section(res: pd.DataFrame, pcol: str, bcol: str, mcol: str, ycol: str, cfg: WalkForwardConfig) -> dict:
    p, y, b = res[pcol].to_numpy(), res[ycol].to_numpy(), res[bcol].to_numpy()
    out = {
        "model": {"brier": brier_score(p, y), "log_loss": log_loss(p, y), "ece": expected_calibration_error(p, y),
                  "auc": roc_auc(p, y), "accuracy": accuracy(p, y)},
        "baseline_frecuencias": {"brier": brier_score(b, y), "log_loss": log_loss(b, y)},
        "vs_baseline_frecuencias": bootstrap_metric_diff(p, b, y, n=cfg.bootstrap, seed=cfg.seed),
        "reliability": [r.__dict__ for r in reliability_curve(p, y)],
    }
    mk = res.dropna(subset=[mcol])
    if len(mk) >= 30:
        pm, m, ym = mk[pcol].to_numpy(), mk[mcol].to_numpy(), mk[ycol].to_numpy()
        out["baseline_mercado"] = {"n": len(mk), "brier": brier_score(m, ym), "log_loss": log_loss(m, ym)}
        out["model_on_market_subset"] = {"brier": brier_score(pm, ym), "log_loss": log_loss(pm, ym)}
        out["vs_baseline_mercado"] = bootstrap_metric_diff(pm, m, ym, n=cfg.bootstrap, seed=cfg.seed)
    return out


def run(matches: pd.DataFrame, odds: pd.DataFrame, model_cfg: dc.DixonColesConfig | None = None,
        cfg: WalkForwardConfig | None = None) -> dict:
    cfg = cfg or WalkForwardConfig()
    model_cfg = model_cfg or dc.DixonColesConfig()
    df = matches.dropna(subset=["home_goals", "away_goals"]).sort_values("kickoff").reset_index(drop=True)
    df["knockout"] = dc.flag_two_leg(df) if model_cfg.knockout else False
    n = len(df)
    start_idx = max(cfg.min_train, n // 3)
    if n <= start_idx + 20:
        return {"status": "insufficient", "reason": f"Solo {n} partidos finalizados; se necesitan > {start_idx + 20}.",
                "n_matches": n}

    test_start = df.loc[start_idx, "kickoff"]
    end = df["kickoff"].max()
    def both(fn, market: str, line: float | None) -> tuple[dict, dict]:
        if odds.empty:
            return {}, {}
        return fn(odds, market, line, closing=False), fn(odds, market, line, closing=True)

    # Referencia de mercado: cuotas pre-cierre si existen; si un partido solo tiene cierre (p. ej. Liga MX
    # en football-data.co.uk), se usa el cierre, que es la referencia más exigente.
    pre_1x2, close_1x2 = both(consensus_fair, "1x2", None)
    pre_ou, close_ou = both(consensus_fair, "totals", 2.5)
    pre_bt, close_bt = both(consensus_fair, "btts", None)
    avg_pre_1x2, avg_close_1x2 = both(average_odds, "1x2", None)
    avg_pre_ou, avg_close_ou = both(average_odds, "totals", 2.5)
    avg_pre_bt, avg_close_bt = both(average_odds, "btts", None)

    def pick(pre: dict, close: dict, ev_id: int) -> dict:
        return pre.get(ev_id) or close.get(ev_id) or {}

    ref_counts = {"pre_cierre": 0, "cierre": 0, "sin_cuotas": 0}

    rows, offers = [], []
    skipped = 0
    refits = 0
    chunk = 0
    chunk_start = test_start
    while chunk_start <= end:
        chunk_end = chunk_start + pd.Timedelta(days=cfg.refit_days)
        train = df[df["kickoff"] < chunk_start]
        test = df[(df["kickoff"] >= chunk_start) & (df["kickoff"] < chunk_end)]
        if not test.empty:
            if train["kickoff"].max() >= test["kickoff"].min():
                raise LeakageError("Datos de entrenamiento posteriores al inicio del bloque de test")
            model = dc.fit(train, chunk_start, model_cfg, compute_uncertainty=False)
            refits += 1
            base = train.assign(r=np.sign(train["home_goals"] - train["away_goals"]))
            base_1x2 = [float((base["r"] > 0).mean()), float((base["r"] == 0).mean()), float((base["r"] < 0).mean())]
            base_over = float(((train["home_goals"] + train["away_goals"]) > 2.5).mean())
            base_btts = float(((train["home_goals"] > 0) & (train["away_goals"] > 0)).mean())
            for _, m in test.iterrows():
                if not (model.has_team(m["home"]) and model.has_team(m["away"])
                        and model.team_sample_ok(m["home"]) and model.team_sample_ok(m["away"])):
                    skipped += 1
                    continue
                sm = model.predict(m["home"], m["away"], bool(m["knockout"]))
                split = adjust.class_split(sm)
                hg, ag = int(m["home_goals"]), int(m["away_goals"])
                y1x2 = 0 if hg > ag else 1 if hg == ag else 2
                yover = int(hg + ag > 2)
                ybtts = int(hg > 0 and ag > 0)
                ev_id = int(m["event_id"])
                m1 = pick(pre_1x2, close_1x2, ev_id)
                ref_counts["pre_cierre" if ev_id in pre_1x2 else "cierre" if ev_id in close_1x2 else "sin_cuotas"] += 1
                rows.append({
                    "event_id": ev_id, "kickoff": m["kickoff"], "chunk": chunk,
                    **{f"c{k}_{i}": float(split[k][i]) for k in ("p", "over", "btts") for i in range(3)},
                    "b_home": base_1x2[0], "b_draw": base_1x2[1], "b_away": base_1x2[2], "b_over": base_over,
                    "m_home": m1.get("home"), "m_draw": m1.get("draw"), "m_away": m1.get("away"),
                    "m_over": pick(pre_ou, close_ou, ev_id).get("over"),
                    "b_btts": base_btts, "m_btts": pick(pre_bt, close_bt, ev_id).get("yes"),
                    "y1x2": y1x2, "yover": yover, "ybtts": ybtts,
                })
                won_1x2 = {"home": y1x2 == 0, "draw": y1x2 == 1, "away": y1x2 == 2}
                candidates = [("1x2", s, avg_pre_1x2, avg_close_1x2, close_1x2, won_1x2[s])
                              for s in ("home", "draw", "away")]
                candidates += [("totals_2.5", s, avg_pre_ou, avg_close_ou, close_ou, (s == "over") == bool(yover))
                               for s in ("over", "under")]
                candidates += [("btts", s, avg_pre_bt, avg_close_bt, close_bt, (s == "yes") == bool(ybtts))
                               for s in ("yes", "no")]
                for market, sel, avg_pre, avg_close, fair_close, won in candidates:
                    d = avg_pre.get(ev_id, {}).get(sel)
                    taken = "pre_cierre"
                    if d is None:
                        d, taken = avg_close.get(ev_id, {}).get(sel), "cierre"
                    if d is None or not (cfg.min_odds <= d <= cfg.max_odds):
                        continue
                    offers.append({"row": len(rows) - 1, "event_id": ev_id, "market": market, "selection": sel,
                                   "odds": d, "taken": taken, "p_close": fair_close.get(ev_id, {}).get(sel),
                                   "won": won})
        chunk_start = chunk_end
        chunk += 1

    res = pd.DataFrame(rows)
    out: dict = {
        "status": "ok", "n_matches": n, "n_eval": len(res), "skipped_insufficient_team_data": skipped,
        "refits": refits, "refit_days": cfg.refit_days,
        "test_period": [test_start.isoformat(), end.isoformat()],
        "leakage_check": "ok: cada reentrenamiento usa solo partidos anteriores al bloque",
        "market_reference": ref_counts,
    }
    if res.empty:
        out["status"] = "insufficient"
        out["reason"] = "Ningún partido evaluable (equipos sin muestra suficiente)."
        return out

    # Ajustes (empates y mercado): para cada bloque se estiman SOLO con predicciones de bloques anteriores
    raw = res[["cp_0", "cp_1", "cp_2"]].to_numpy()
    raw = raw / raw.sum(axis=1, keepdims=True)
    mkt = res[["m_home", "m_draw", "m_away"]].to_numpy(dtype=float)
    yy = res["y1x2"].to_numpy()
    final = raw.copy()
    if cfg.draw_adjust or cfg.market_blend:
        for c in sorted(res["chunk"].unique()):
            prev, cur = (res["chunk"] < c).to_numpy(), (res["chunk"] == c).to_numpy()
            a = adjust.fit(raw[prev], mkt[prev], yy[prev], cfg.draw_adjust, cfg.market_blend)
            final[cur] = adjust.class_probs(raw[cur], a, mkt[cur])
        out["adjustment"] = adjust.fit(raw, mkt, yy, cfg.draw_adjust, cfg.market_blend).to_dict()
    res["p_home"], res["p_draw"], res["p_away"] = final[:, 0], final[:, 1], final[:, 2]
    res["p_over"] = adjust.derived(raw, final, res[["cover_0", "cover_1", "cover_2"]].to_numpy())
    res["p_btts"] = adjust.derived(raw, final, res[["cbtts_0", "cbtts_1", "cbtts_2"]].to_numpy())

    bets = []
    for o in offers:
        r = res.iloc[o["row"]]
        pm = {("1x2", "home"): r.p_home, ("1x2", "draw"): r.p_draw, ("1x2", "away"): r.p_away,
              ("totals_2.5", "over"): r.p_over, ("totals_2.5", "under"): 1 - r.p_over,
              ("btts", "yes"): r.p_btts, ("btts", "no"): 1 - r.p_btts}[(o["market"], o["selection"])]
        d = o["odds"]
        if pm * d - 1 >= cfg.min_ev:
            # CLV: cuota tomada vs cuota justa de cierre (no aplica si se apostó al cierre)
            clv = (d * o["p_close"] - 1) if (o["p_close"] and o["taken"] == "pre_cierre") else None
            bets.append({"event_id": o["event_id"], "market": o["market"], "selection": o["selection"], "odds": d,
                         "p": pm, "profit": d - 1 if o["won"] else -1.0, "clv": clv, "taken": o["taken"]})

    P = res[["p_home", "p_draw", "p_away"]].to_numpy()
    B = res[["b_home", "b_draw", "b_away"]].to_numpy()
    y = res["y1x2"].to_numpy()
    out["1x2"] = {
        "model": {"brier": brier_score(P, y), "log_loss": log_loss(P, y), "ece": multiclass_ece(P, y),
                  "accuracy": accuracy(P, y)},
        "baseline_frecuencias": {"brier": brier_score(B, y), "log_loss": log_loss(B, y)},
        "vs_baseline_frecuencias": bootstrap_metric_diff(P, B, y, n=cfg.bootstrap, seed=cfg.seed),
        "reliability": [b.__dict__ for b in reliability_curve(P[:, 0], y == 0)],
        "reliability_note": "Curva de calibración de la probabilidad de victoria local.",
    }
    if cfg.draw_adjust or cfg.market_blend:
        out["1x2"]["model_raw"] = {"brier": brier_score(raw, y), "log_loss": log_loss(raw, y)}
    mk = res.dropna(subset=["m_home", "m_draw", "m_away"])
    if len(mk) >= 30:
        Pm = mk[["p_home", "p_draw", "p_away"]].to_numpy()
        M = mk[["m_home", "m_draw", "m_away"]].to_numpy()
        ym = mk["y1x2"].to_numpy()
        out["1x2"]["baseline_mercado"] = {"n": len(mk), "brier": brier_score(M, ym), "log_loss": log_loss(M, ym)}
        out["1x2"]["model_on_market_subset"] = {"brier": brier_score(Pm, ym), "log_loss": log_loss(Pm, ym)}
        out["1x2"]["vs_baseline_mercado"] = bootstrap_metric_diff(Pm, M, ym, n=cfg.bootstrap, seed=cfg.seed)

    out["totals_2.5"] = _binary_section(res, "p_over", "b_over", "m_over", "yover", cfg)
    out["btts"] = _binary_section(res, "p_btts", "b_btts", "m_btts", "ybtts", cfg)

    # Probabilidades fuera de muestra por partido (para la pantalla de resultados). La clave "_oos" no se
    # guarda ni se publica: el servicio la retira antes de persistir el walk-forward.
    out["_oos"] = {int(r.event_id): {"1x2": [float(r.p_home), float(r.p_draw), float(r.p_away)],
                                     "over25": float(r.p_over), "btts": float(r.p_btts)}
                   for r in res.itertuples()}

    synthetic_odds = bool(odds["is_synthetic"].any()) if not odds.empty else False
    out["odds_kind"] = "none" if odds.empty else ("sintetica" if synthetic_odds else "real")
    out["betting"] = betting_summary(pd.DataFrame(bets), cfg.bootstrap, cfg.seed) if bets else {"n_bets": 0}
    closing_only = ref_counts["cierre"] > 0 and ref_counts["pre_cierre"] == 0
    out["betting"]["note"] = (
        "Sin cuotas históricas: no se mide rentabilidad." if odds.empty else
        "Cuotas SINTÉTICAS: resultado económico hipotético, no evidencia de rentabilidad." if synthetic_odds else
        "Cuotas históricas reales de CIERRE (media de operadores): simula apostar al cierre; el CLV no aplica."
        if closing_only else
        "Cuotas históricas reales (media de operadores, pre-cierre con timestamp aproximado; "
        "cierre cuando no hay pre-cierre)."
    )
    return out
