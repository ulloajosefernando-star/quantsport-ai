"""Resultados pasados: marcador, mercado al cierre, modelo fuera de muestra y pronósticos registrados."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calibration.metrics import log_loss
from app.core.config import ValueThresholds
from app.db.models import Competition, Event, Prediction, Settlement
from app.ingestion.store import as_utc
from app.markets import registry
from app.markets.quotes import selection_label
from app.markets.registry import MarketValidationError
from app.sports.football.data import average_odds, consensus_fair, finished, matches_frame, odds_frame
from app.sports.football.service import football_service

SEL_1X2 = ("home", "draw", "away")
SEL_2WAY = ("home", "away")
WON_STATES = ("gana", "medio_gana")


def default_competition(s: Session) -> Competition | None:
    """La competición real con más partidos jugados (o la sintética si no hay datos reales)."""
    rows = s.execute(
        select(Competition, func.count(Event.id)).join(Event, Event.competition_id == Competition.id)
        .where(Event.status == "finished").group_by(Competition.id)
    ).all()
    if not rows:
        return None
    return max(rows, key=lambda r: (not r[0].is_synthetic, r[0].sport.key == "football", r[1]))[0]


def _round(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def _probs(d: dict | None, sels: tuple[str, ...]) -> list[float] | None:
    if not d or any(d.get(k) is None for k in sels):
        return None
    return [round(float(d[k]), 4) for k in sels]


def _last_before_kickoff(fn, odds: pd.DataFrame, market: str, line: float | None) -> dict[int, dict[str, float]]:
    """Cierre si lo hay; si no, la última cuota capturada antes del partido (la del programador)."""
    if odds.empty:
        return {}
    return {**fn(odds, market, line, False), **fn(odds, market, line, True)}


def _outcome(hg: int, ag: int) -> str:
    return "home" if hg > ag else "draw" if hg == ag else "away"


def _label(market: str, selection: str, line: float | None, home: str, away: str) -> str:
    try:
        return selection_label(registry.get("football", market), selection, line, home, away)
    except MarketValidationError:
        return f"{market} {selection}" + (f" {line:g}" if line is not None else "")


def _predictions_by_event(s: Session, names: dict[int, tuple[str, str]]) -> dict[int, list[dict]]:
    event_ids = list(names)
    out: dict[int, list[dict]] = {}
    if not event_ids:
        return out
    rows = s.execute(
        select(Prediction, Settlement).outerjoin(Settlement, Settlement.prediction_id == Prediction.id)
        .where(Prediction.event_id.in_(event_ids)).order_by(Prediction.created_at)
    ).all()
    for p, st in rows:
        out.setdefault(p.event_id, []).append({
            "market": p.market, "line": p.line, "selection": p.selection,
            "label": _label(p.market, p.selection, p.line, *names[p.event_id]),
            "p_model": _round(p.p_model), "odds": p.odds, "bookmaker": p.bookmaker, "ev": _round(p.ev),
            "category": p.category, "closing_odds": p.closing_odds,
            "clv": _round(p.odds / p.closing_odds - 1) if p.odds and p.closing_odds else None,
            "state": st.state if st else "pendiente", "profit": _round(st.net_return) if st else None,
        })
    return out


def results(s: Session, th: ValueThresholds, tz: str, competition_key: str | None = None, team: str | None = None,
            date_from: date | None = None, date_to: date | None = None, page: int = 1, page_size: int = 50) -> dict:
    comps = [{"key": c.key, "name": c.name, "is_synthetic": c.is_synthetic, "sport": c.sport.key}
             for c in s.scalars(select(Competition).order_by(Competition.name))]
    comp = (s.scalar(select(Competition).where(Competition.key == competition_key)) if competition_key
            else default_competition(s))
    empty = {"competitions": comps, "competition": None, "teams": [], "items": [], "total": 0, "page": page,
             "page_size": page_size, "summary": {"n_matches": 0}}
    if comp is None:
        return empty
    df = finished(matches_frame(s, comp.id))
    teams = sorted(set(df["home"]) | set(df["away"])) if not df.empty else []
    zone = ZoneInfo(tz)
    if not df.empty and team:
        df = df[(df["home"] == team) | (df["away"] == team)]
    if not df.empty and date_from:
        df = df[df["kickoff"] >= pd.Timestamp(datetime.combine(date_from, time.min, zone))]
    if not df.empty and date_to:
        df = df[df["kickoff"] <= pd.Timestamp(datetime.combine(date_to, time.max, zone))]
    nba = comp.sport.key == "nba"
    sels = SEL_2WAY if nba else SEL_1X2
    base = {**empty, "competition": {"key": comp.key, "name": comp.name, "is_synthetic": comp.is_synthetic,
                                     "sport": comp.sport.key}, "teams": teams, "selections": list(sels)}
    if df.empty:
        return base
    df = df.sort_values("kickoff", ascending=False)
    ids = df["event_id"].astype(int).tolist()

    odds = odds_frame(s, ids)
    main = "moneyline" if nba else "1x2"
    fair_1x2 = _last_before_kickoff(consensus_fair, odds, main, None)
    avg_1x2 = _last_before_kickoff(average_odds, odds, main, None)
    fair_ou = {} if nba else _last_before_kickoff(consensus_fair, odds, "totals", 2.5)
    fair_bt = {} if nba else _last_before_kickoff(consensus_fair, odds, "btts", None)
    if nba:
        from app.sports.nba.service import nba_service

        oos = {k: {"1x2": v["moneyline"], "mu_margin": v["mu_margin"], "mu_total": v["mu_total"]}
               for k, v in nba_service.state(s, comp, th).oos.items()}
    else:
        oos = football_service.state(s, comp, th).oos

    hg, ag = df["home_goals"].astype(int), df["away_goals"].astype(int)
    goals = hg + ag
    summary: dict = {
        "n_matches": len(df), "home_win": float((hg > ag).mean()), "draw": float((hg == ag).mean()),
        "away_win": float((hg < ag).mean()), "avg_goals": float(goals.mean()),
        "over_2_5": None if nba else float((goals > 2.5).mean()),
        "btts": None if nba else float(((hg > 0) & (ag > 0)).mean()),
    }
    if team:
        is_home = df["home"] == team
        gf = np.where(is_home, hg, ag)
        ga = np.where(is_home, ag, hg)
        summary["team"] = {"name": team, "won": int((gf > ga).sum()), "drawn": int((gf == ga).sum()),
                           "lost": int((gf < ga).sum()), "goals_for": int(gf.sum()), "goals_against": int(ga.sum())}

    # Modelo (fuera de muestra) contra el mercado al cierre, en los partidos donde hay ambos
    y = [sels.index(_outcome(h, a)) for h, a in zip(hg, ag, strict=True)]
    P, M, Y = [], [], []
    hits = 0
    n_model = 0
    for ev_id, yi in zip(ids, y, strict=True):
        pm = (oos.get(ev_id) or {}).get("1x2")
        if pm is None:
            continue
        n_model += 1
        hits += int(int(np.argmax(pm)) == yi)
        mk = _probs(fair_1x2.get(ev_id), sels)
        if mk is not None:
            P.append(pm)
            M.append(mk)
            Y.append(yi)
    model_vs_market: dict = {"n_model": n_model, "model_accuracy": hits / n_model if n_model else None}
    if len(Y) >= 10:
        Pa, Ma, Ya = np.array(P), np.array(M), np.array(Y)
        model_vs_market |= {"n": len(Y), "model_log_loss": log_loss(Pa, Ya), "market_log_loss": log_loss(Ma, Ya),
                            "market_accuracy": float((Ma.argmax(1) == Ya).mean())}
    summary["model_vs_market"] = model_vs_market

    preds = _predictions_by_event(s, {int(r.event_id): (r.home, r.away) for r in df.itertuples()})
    settled = [p for ps in preds.values() for p in ps if p["state"] not in ("pendiente", "anulada")]
    if settled:
        clvs = [p["clv"] for p in settled if p["clv"] is not None]
        summary["predictions"] = {
            "n": len(settled), "hit_rate": sum(p["state"] in WON_STATES for p in settled) / len(settled),
            "roi": sum(p["profit"] or 0 for p in settled) / len(settled),
            "avg_clv": float(np.mean(clvs)) if clvs else None, "n_clv": len(clvs),
        }
    else:
        summary["predictions"] = {"n": 0}

    page = max(page, 1)
    chunk = df.iloc[(page - 1) * page_size: page * page_size]
    items = []
    for r in chunk.itertuples():
        ev_id, h, a = int(r.event_id), int(r.home_goals), int(r.away_goals)
        o = oos.get(ev_id) or {}
        pm = o.get("1x2")
        ev = s.get(Event, ev_id)
        kick = as_utc(r.kickoff.to_pydatetime())
        items.append({
            "event_id": ev_id, "kickoff_utc": kick.isoformat(), "kickoff_local": kick.astimezone(zone).isoformat(),
            "home": r.home, "away": r.away, "home_score": h, "away_score": a,
            "ht": [ev.home_score_ht, ev.away_score_ht] if ev and ev.home_score_ht is not None else None,
            "outcome": _outcome(h, a), "total_goals": h + a, "btts": h > 0 and a > 0,
            "market_1x2": _probs(fair_1x2.get(ev_id), sels),
            "closing_odds_1x2": _probs(avg_1x2.get(ev_id), sels),
            "market_over25": _round((fair_ou.get(ev_id) or {}).get("over")),
            "market_btts": _round((fair_bt.get(ev_id) or {}).get("yes")),
            "model_1x2": [round(x, 4) for x in pm] if pm else None,
            "model_over25": _round(o.get("over25")), "model_btts": _round(o.get("btts")),
            "model_pick": sels[int(np.argmax(pm))] if pm else None,
            "model_hit": (sels[int(np.argmax(pm))] == _outcome(h, a)) if pm else None,
            "model_margin": _round(o.get("mu_margin"), 1), "model_total": _round(o.get("mu_total"), 1),
            "predictions": preds.get(ev_id, []),
        })
    return {**base, "items": items, "total": len(df), "page": page, "summary": summary,
            "is_synthetic": comp.is_synthetic,
            "notes": [
                "Mercado: probabilidad sin margen de las cuotas de cierre (media de operadores); si no hay cierre, "
                "la última cuota guardada antes del partido.",
                "Modelo: probabilidad fuera de muestra del walk-forward; cada partido se predijo solo con "
                "partidos anteriores. Los primeros partidos de la historia no tienen predicción.",
                *(["NBA: no hay cuotas históricas gratuitas; el mercado aparece solo en partidos cuyas cuotas "
                   "guardó el programador."] if nba else
                  ["Pronósticos: los que registró el programador antes del partido, con su liquidación y CLV."]),
            ]}
