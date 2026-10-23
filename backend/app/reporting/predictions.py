"""Registro, liquidación y rendimiento de pronósticos emitidos.

ROI = beneficio neto / capital apostado (stakes planos de 1 unidad por pronóstico registrado,
solo para categorías accionables). Los push devuelven el stake (retorno 0) y cuentan como
apostados; las apuestas anuladas (evento cancelado) se excluyen del capital apostado.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.football_walkforward import betting_summary
from app.calibration.metrics import brier_score, log_loss
from app.db.models import Event, Prediction, Settlement
from app.probability.settlement import handicap_return, total_return

ACTIONABLE = {"candidato_valor", "en_observacion"}


def snapshot(s: Session, rows: list[dict], model_version_ids: dict[str, int]) -> dict:
    """Guarda el ranking actual como pronósticos emitidos (antes del inicio)."""
    inserted = dup = skipped = 0
    now = datetime.now(UTC)
    for r in rows:
        if r.get("odds") is None or r.get("p_model") is None:
            skipped += 1
            continue
        if datetime.fromisoformat(r["kickoff_utc"]) <= now:
            skipped += 1  # nunca registrar después del inicio
            continue
        mv_id = model_version_ids.get(r["competition_key"])
        if mv_id is None:
            skipped += 1
            continue
        captured = datetime.fromisoformat(r["odds_captured_at"]) if r.get("odds_captured_at") else None
        line = r.get("line")
        exists = s.scalar(select(Prediction.id).where(
            Prediction.event_id == r["event_id"], Prediction.model_version_id == mv_id,
            Prediction.market == r["market"], Prediction.selection == r["selection"],
            Prediction.bookmaker == (r.get("bookmaker") or ""),
            Prediction.line.is_(None) if line is None else Prediction.line == line,
            Prediction.odds_captured_at.is_(None) if captured is None else Prediction.odds_captured_at == captured,
        ))
        if exists:
            dup += 1  # la restricción UNIQUE no cubre NULL en SQL: se deduplica explícitamente
            continue
        p = Prediction(
            event_id=r["event_id"], model_version_id=mv_id, market=r["market"], line=r.get("line"),
            selection=r["selection"], p_model=r["p_model"],
            p_low=(r.get("p_model_ci90") or [None, None])[0], p_high=(r.get("p_model_ci90") or [None, None])[1],
            p_market_fair=r.get("p_market_fair"), odds=r["odds"], bookmaker=r.get("bookmaker") or "",
            odds_captured_at=captured,
            fair_odds=r.get("fair_odds"), min_odds=r.get("min_odds"), ev=r.get("ev"),
            ev_low=(r.get("ev_ci90") or [None, None])[0], ev_high=(r.get("ev_ci90") or [None, None])[1],
            edge=r.get("edge"), category=r["category"],
            explanation={"reasons": r.get("reasons", []), "risks": r.get("risks", []),
                         "definitions": r.get("definitions", {}), "quality_score": r.get("quality_score")},
            is_synthetic=bool(r.get("is_synthetic")),
        )
        s.add(p)
        s.flush()
        inserted += 1
    s.commit()
    return {"inserted": inserted, "duplicates": dup, "skipped": skipped}


def settle_one(p: Prediction, ev: Event) -> tuple[str, float, float | None]:
    """Devuelve (estado, retorno_neto, resultado binario para métricas o None)."""
    hg, ag = ev.home_score, ev.away_score
    d = p.odds or 2.0
    if p.market == "1x2":
        won = (p.selection == "home" and hg > ag) or (p.selection == "draw" and hg == ag) or \
              (p.selection == "away" and hg < ag)
        return ("gana", d - 1, 1.0) if won else ("pierde", -1.0, 0.0)
    if p.market == "btts":
        yes = hg > 0 and ag > 0
        won = yes if p.selection == "yes" else not yes
        return ("gana", d - 1, 1.0) if won else ("pierde", -1.0, 0.0)
    if p.market == "totals":
        name, ret = total_return(hg + ag, float(p.line), d, p.selection)
        binary = None if name not in ("gana", "pierde") else float(name == "gana")
        return name, ret, binary
    if p.market == "ah":
        diff = hg - ag if p.selection == "home" else ag - hg
        name, ret = handicap_return(diff, float(p.line), d)
        return name, ret, None
    raise ValueError(f"Mercado sin regla de liquidación: {p.market}")


def settle_pending(s: Session) -> dict:
    q = (select(Prediction, Event).join(Event, Prediction.event_id == Event.id)
         .outerjoin(Settlement, Settlement.prediction_id == Prediction.id)
         .where(Settlement.id.is_(None), Event.status.in_(["finished", "cancelled"])))
    n = 0
    for p, ev in s.execute(q).all():
        if ev.status == "cancelled":
            s.add(Settlement(prediction_id=p.id, state="anulada", net_return=0.0))
        else:
            state, ret, _ = settle_one(p, ev)
            s.add(Settlement(prediction_id=p.id, state=state, net_return=ret))
        n += 1
    s.commit()
    return {"settled": n}


def performance(s: Session) -> dict:
    rows = s.execute(
        select(Prediction, Settlement, Event).join(Settlement, Settlement.prediction_id == Prediction.id)
        .join(Event, Prediction.event_id == Event.id)
    ).all()
    total_registered = len(list(s.scalars(select(Prediction.id))))
    if not rows:
        return {"registered": total_registered, "settled": 0,
                "note": "Aún no hay pronósticos liquidados. Registra snapshots antes de los partidos."}
    import pandas as pd

    recs = []
    for p, st, ev in rows:
        _, _, binary = settle_one(p, ev) if st.state != "anulada" else (None, None, None)
        recs.append({"category": p.category, "market": p.market, "odds": p.odds, "p": p.p_model,
                     "profit": st.net_return, "state": st.state, "binary": binary,
                     "clv": (p.odds / p.closing_odds - 1) if p.closing_odds else None,
                     "model_version_id": p.model_version_id, "is_synthetic": p.is_synthetic})
    df = pd.DataFrame(recs)
    out = {"registered": total_registered, "settled": len(df),
           "synthetic_share": float(df["is_synthetic"].mean())}
    act = df[df["category"].isin(ACTIONABLE) & (df["state"] != "anulada")]
    out["actionable"] = betting_summary(act) if not act.empty else {"n_bets": 0}
    b = df.dropna(subset=["binary"])
    if not b.empty:
        out["probability_quality"] = {
            "n": len(b), "brier": brier_score(b["p"].to_numpy(), b["binary"].to_numpy()),
            "log_loss": log_loss(b["p"].to_numpy(), b["binary"].to_numpy()),
            "mean_p": float(b["p"].mean()), "hit_rate": float(b["binary"].mean()),
        }
    out["by_category"] = {k: {"n": len(g), "roi": float(g["profit"].sum() / max(len(g), 1))}
                          for k, g in df[df["state"] != "anulada"].groupby("category")}
    out["by_model_version"] = {str(k): {"n": len(g), "roi": float(g["profit"].sum() / max(len(g), 1))}
                               for k, g in df[df["state"] != "anulada"].groupby("model_version_id")}
    out["note"] = "ROI sobre capital apostado (1 unidad por pronóstico). Muestra pequeña ⇒ alta incertidumbre."
    if out["synthetic_share"] > 0:
        out["warning"] = "Incluye pronósticos sobre datos SINTÉTICOS: no es evidencia de rendimiento real."
    _ = np  # reservado para métricas adicionales
    return out
