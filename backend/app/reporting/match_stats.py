"""Estadística simple de un partido con resultados pasados (sin modelo).

Para cada partido:

* Forma: últimos resultados de cada equipo y enfrentamientos directos.
* Local en casa y visitante fuera: porcentaje de victorias, empates y derrotas, goles a favor y en contra,
  partidos con más de 2.5 goles y con ambos anotando, en sus últimos ``N_VENUE`` partidos en esa condición.
* Probabilidad estadística: promedio de lo que hace el local en casa y lo que le pasa al visitante fuera.
  Por ejemplo, P(gana local) = (victorias del local en casa + derrotas del visitante fuera) / 2. Con pocos
  partidos se acerca a la media de la liga (``SHRINK`` partidos ficticios con la media de la liga).

El mismo cálculo se repite para todos los partidos jugados, cada uno solo con partidos anteriores, para medir
cuánto acierta este método y compararlo con el mercado (las cuotas sin margen).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.calibration.metrics import accuracy, log_loss
from app.db.models import Event
from app.ingestion.store import as_utc
from app.sports.football.data import consensus_fair, finished, matches_frame, odds_frame

N_FORM = 10
N_VENUE = 15
N_H2H = 6
SHRINK = 5.0
MIN_VENUE = 5
STATS = ("win", "draw", "loss", "gf", "ga", "over", "btts")

_cache: dict[tuple, dict] = {}


def _long(df: pd.DataFrame) -> pd.DataFrame:
    """Una fila por equipo y partido, desde el punto de vista de ese equipo."""
    h = pd.DataFrame({"event_id": df["event_id"], "kickoff": df["kickoff"], "team": df["home"], "opp": df["away"],
                      "venue": "home", "gf": df["home_goals"], "ga": df["away_goals"]})
    a = pd.DataFrame({"event_id": df["event_id"], "kickoff": df["kickoff"], "team": df["away"], "opp": df["home"],
                      "venue": "away", "gf": df["away_goals"], "ga": df["home_goals"]})
    out = pd.concat([h, a], ignore_index=True)
    out["gf"], out["ga"] = out["gf"].astype(float), out["ga"].astype(float)
    out["win"] = (out["gf"] > out["ga"]).astype(float)
    out["draw"] = (out["gf"] == out["ga"]).astype(float)
    out["loss"] = (out["gf"] < out["ga"]).astype(float)
    out["over"] = ((out["gf"] + out["ga"]) > 2.5).astype(float)
    out["btts"] = ((out["gf"] > 0) & (out["ga"] > 0)).astype(float)
    return out.sort_values("kickoff", kind="stable").reset_index(drop=True)


def _summary(rows: pd.DataFrame) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    return {"n": n, **{k: float(rows[k].mean()) for k in STATS},
            "clean_sheet": float((rows["ga"] == 0).mean()), "failed_to_score": float((rows["gf"] == 0).mean())}


def _league(df: pd.DataFrame) -> dict:
    hg, ag = df["home_goals"].astype(float), df["away_goals"].astype(float)
    return {"home": float((hg > ag).mean()), "draw": float((hg == ag).mean()), "away": float((hg < ag).mean()),
            "over": float(((hg + ag) > 2.5).mean()), "btts": float(((hg > 0) & (ag > 0)).mean()),
            "home_goals": float(hg.mean()), "away_goals": float(ag.mean()), "n": len(df)}


def _shrink(rate, n, prior):
    return (rate * n + SHRINK * prior) / (n + SHRINK)


def probabilities(home: dict, away: dict, lg: dict) -> dict:
    """Probabilidad estadística a partir del local en casa y el visitante fuera (encogida a la liga)."""
    nh, na = home["n"], away["n"]

    def s(d, k, n, prior):
        return _shrink(d[k], n, prior) if n else prior

    p_home = (s(home, "win", nh, lg["home"]) + s(away, "loss", na, lg["home"])) / 2
    p_draw = (s(home, "draw", nh, lg["draw"]) + s(away, "draw", na, lg["draw"])) / 2
    p_away = (s(home, "loss", nh, lg["away"]) + s(away, "win", na, lg["away"])) / 2
    tot = p_home + p_draw + p_away
    return {"1x2": {"home": p_home / tot, "draw": p_draw / tot, "away": p_away / tot},
            "over_2_5": (s(home, "over", nh, lg["over"]) + s(away, "over", na, lg["over"])) / 2,
            "btts": (s(home, "btts", nh, lg["btts"]) + s(away, "btts", na, lg["btts"])) / 2,
            "goals_home": (s(home, "gf", nh, lg["home_goals"]) + s(away, "ga", na, lg["home_goals"])) / 2,
            "goals_away": (s(home, "ga", nh, lg["away_goals"]) + s(away, "gf", na, lg["away_goals"])) / 2}


def _form(rows: pd.DataFrame) -> list[dict]:
    return [{"date": as_utc(r.kickoff.to_pydatetime()).date().isoformat(), "opponent": r.opp, "venue": r.venue,
             "score": f"{int(r.gf)}-{int(r.ga)}", "result": "G" if r.win else "E" if r.draw else "P"}
            for r in rows.iloc[::-1].itertuples()]


def backtest(df: pd.DataFrame, odds: pd.DataFrame) -> dict:
    """Aplica el método a cada partido jugado usando solo partidos anteriores y lo compara con el mercado."""
    df = df.sort_values("kickoff").reset_index(drop=True)
    if len(df) < 100:
        return {"status": "insufficient", "n": len(df)}
    lg = df.assign(hg=df["home_goals"].astype(float), ag=df["away_goals"].astype(float))
    prior = pd.DataFrame({
        "home": (lg["hg"] > lg["ag"]).astype(float), "draw": (lg["hg"] == lg["ag"]).astype(float),
        "away": (lg["hg"] < lg["ag"]).astype(float), "over": ((lg["hg"] + lg["ag"]) > 2.5).astype(float),
        "btts": ((lg["hg"] > 0) & (lg["ag"] > 0)).astype(float)}).shift(1).expanding().mean()
    long = _long(df)
    roll = (long.groupby(["team", "venue"])[list(STATS)]
            .transform(lambda c: c.shift(1).rolling(N_VENUE, min_periods=1).mean()))
    roll["n"] = long.groupby(["team", "venue"]).cumcount().clip(upper=N_VENUE)
    roll[["event_id", "venue"]] = long[["event_id", "venue"]]
    H = roll[roll["venue"] == "home"].set_index("event_id")
    A = roll[roll["venue"] == "away"].set_index("event_id")
    ids = df["event_id"].to_numpy()
    H, A = H.loc[ids], A.loc[ids]
    ok = (H["n"].to_numpy() >= MIN_VENUE) & (A["n"].to_numpy() >= MIN_VENUE) & prior["home"].notna().to_numpy()

    def sh(frame, col, prior_col):
        return _shrink(frame[col].to_numpy(), frame["n"].to_numpy(), prior[prior_col].to_numpy())

    P = np.column_stack([(sh(H, "win", "home") + sh(A, "loss", "home")) / 2,
                         (sh(H, "draw", "draw") + sh(A, "draw", "draw")) / 2,
                         (sh(H, "loss", "away") + sh(A, "win", "away")) / 2])
    P = P / P.sum(axis=1, keepdims=True)
    over = (sh(H, "over", "over") + sh(A, "over", "over")) / 2
    btts = (sh(H, "btts", "btts") + sh(A, "btts", "btts")) / 2
    hg, ag = df["home_goals"].to_numpy(float), df["away_goals"].to_numpy(float)
    y = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
    yo, yb = (hg + ag > 2.5).astype(int), ((hg > 0) & (ag > 0)).astype(int)
    P, over, btts, y, yo, yb, ids = P[ok], over[ok], btts[ok], y[ok], yo[ok], yb[ok], ids[ok]
    out: dict = {"status": "ok", "n": int(ok.sum()),
                 "1x2": {"accuracy": accuracy(P, y), "log_loss": log_loss(P, y)},
                 "over_2_5": {"accuracy": accuracy(over, yo), "log_loss": log_loss(over, yo)},
                 "btts": {"accuracy": accuracy(btts, yb), "log_loss": log_loss(btts, yb)}}
    if not odds.empty:
        fair = {**consensus_fair(odds, "1x2", None, True), **consensus_fair(odds, "1x2", None, False)}
        idx = [i for i, e in enumerate(ids) if int(e) in fair]
        if len(idx) >= 30:
            M = np.array([[fair[int(ids[i])][s] for s in ("home", "draw", "away")] for i in idx])
            out["1x2"]["market"] = {"n": len(idx), "accuracy": accuracy(M, y[idx]), "log_loss": log_loss(M, y[idx]),
                                    "method_accuracy": accuracy(P[idx], y[idx]),
                                    "method_log_loss": log_loss(P[idx], y[idx])}
    return out


def match_stats(s: Session, ev: Event) -> dict:
    comp = ev.competition
    kickoff = as_utc(ev.kickoff_utc)
    df = finished(matches_frame(s, comp.id))
    df = df[df["kickoff"] < pd.Timestamp(kickoff)] if not df.empty else df
    home, away = ev.home_team.name, ev.away_team.name
    out: dict = {"home": home, "away": away, "competition": comp.name, "n_matches": len(df)}
    if len(df) < 30:
        out["reason"] = "Pocos partidos jugados en esta competición para calcular estadísticas."
        return out
    long = _long(df)
    th, ta = long[long["team"] == home], long[long["team"] == away]
    h_home, a_away = th[th["venue"] == "home"].tail(N_VENUE), ta[ta["venue"] == "away"].tail(N_VENUE)
    lg = _league(df)
    sh, sa = _summary(h_home), _summary(a_away)
    pairs = df[((df["home"] == home) & (df["away"] == away)) | ((df["home"] == away) & (df["away"] == home))]
    out |= {
        "league": lg,
        "form": {"home": _form(th.tail(N_FORM)), "away": _form(ta.tail(N_FORM))},
        "form_summary": {"home": _summary(th.tail(N_FORM)), "away": _summary(ta.tail(N_FORM))},
        "venue": {"home": sh, "away": sa},
        "h2h": [{"date": as_utc(r.kickoff.to_pydatetime()).date().isoformat(), "home": r.home, "away": r.away,
                 "score": f"{int(r.home_goals)}-{int(r.away_goals)}"} for r in pairs.tail(N_H2H).iloc[::-1].itertuples()],
        "probabilities": probabilities(sh, sa, lg),
        "low_sample": sh["n"] < MIN_VENUE or sa["n"] < MIN_VENUE,
        "params": {"n_form": N_FORM, "n_venue": N_VENUE, "shrink": SHRINK},
    }
    odds = odds_frame(s, [ev.id])
    live = odds[~odds["is_closing"]] if not odds.empty else odds
    if not live.empty:
        f = next(iter(consensus_fair(live, "1x2", None, False).values()), None)
        if f:
            out["market_1x2"] = f
    out["backtest"] = _backtest_cached(s, comp.id)
    return out


def _backtest_cached(s: Session, comp_id: int) -> dict:
    df = finished(matches_frame(s, comp_id))
    if df.empty:
        return {"status": "insufficient", "n": 0}
    key = (comp_id, len(df), str(df["kickoff"].max()), int(df["home_goals"].sum() + df["away_goals"].sum()))
    if key not in _cache:
        if len(_cache) > 20:
            _cache.clear()
        odds = odds_frame(s, df["event_id"].astype(int).tolist())
        _cache[key] = backtest(df, odds) | {"computed_at": datetime.now().isoformat(timespec="seconds")}
    return _cache[key]
