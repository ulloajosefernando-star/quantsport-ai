"""Comparación de variantes del modelo de fútbol con el mismo walk-forward (contra el cierre si hay cuotas).

Cada variante cambia una sola cosa respecto a la base, salvo las combinadas. Se elige una variante solo si
mejora el log loss 1X2 de forma clara (no basta con una diferencia dentro del ruido) y no empeora la
calibración. Elegir entre pocas variantes con los mismos datos tiene algo de sesgo de selección: por eso la
mejora debe ser clara, y la validación sigue corriendo cada día con datos nuevos.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.backtesting import football_walkforward as wf
from app.sports.football import dixon_coles as dc


def xi(half_life_days: float) -> float:
    return math.log(2) / half_life_days


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    model: dict = field(default_factory=dict)
    wf: dict = field(default_factory=dict)


VARIANTS: list[Variant] = [
    Variant("base", "Modelo actual"),
    Variant("semivida_180", "Más peso a lo reciente (semivida 180 días)", {"xi_per_day": xi(180)}),
    Variant("semivida_730", "Menos peso a lo reciente (semivida 2 años)", {"xi_per_day": xi(730)}),
    Variant("localia_equipo", "Localía propia de cada equipo (altitud, viaje)", {"team_home": True}),
    Variant("torneo_08", "Torneo anterior pesa 20 % menos", {"tournament_decay": 0.8}),
    Variant("torneo_06", "Torneo anterior pesa 40 % menos", {"tournament_decay": 0.6}),
    Variant("eliminatoria", "Localía distinta en eliminatorias (Liguilla)", {"knockout": True}),
    Variant("empates", "Calibración de empates", wf={"draw_adjust": True}),
    Variant("ajustes", "Localía propia + torneo 0.8 + Liguilla + empates",
            {"team_home": True, "tournament_decay": 0.8, "knockout": True}, {"draw_adjust": True}),
    Variant("mercado", "Modelo actual combinado con el mercado", wf={"market_blend": True}),
    Variant("ajustes_mercado", "Todos los ajustes combinados con el mercado",
            {"team_home": True, "tournament_decay": 0.8, "knockout": True},
            {"draw_adjust": True, "market_blend": True}),
]


def summarize(v: Variant, out: dict) -> dict:
    row: dict = {"key": v.key, "label": v.label, "status": out.get("status")}
    if out.get("status") != "ok":
        row["reason"] = out.get("reason")
        return row
    s = out["1x2"]
    row |= {"n": out["n_eval"], "log_loss": s["model"]["log_loss"], "brier": s["model"]["brier"],
            "ece": s["model"]["ece"], "accuracy": s["model"]["accuracy"],
            "ou_log_loss": out["totals_2.5"]["model"]["log_loss"], "btts_log_loss": out["btts"]["model"]["log_loss"]}
    if "baseline_mercado" in s:
        vm = s["vs_baseline_mercado"]
        row |= {"n_market": s["baseline_mercado"]["n"], "market_log_loss": s["baseline_mercado"]["log_loss"],
                "model_log_loss_subset": s["model_on_market_subset"]["log_loss"],
                "vs_market": [vm["diff"], vm["low"], vm["high"]]}
    b = out.get("betting") or {}
    if b.get("n_bets"):
        row |= {"n_bets": b["n_bets"], "roi": b["roi"], "roi_ci90": b.get("roi_ci90")}
    if "adjustment" in out:
        row["adjustment"] = out["adjustment"]
    if "model_raw" in s:
        row["log_loss_raw"] = s["model_raw"]["log_loss"]
    return row


def run(matches: pd.DataFrame, odds: pd.DataFrame, variants: list[Variant] | None = None,
        base_model: dict | None = None, base_wf: dict | None = None,
        progress: Callable[[str], None] | None = None) -> list[dict]:
    rows = []
    for v in variants or VARIANTS:
        if progress:
            progress(v.label)
        cfg = dc.DixonColesConfig(**((base_model or {}) | v.model))
        wcfg = wf.WalkForwardConfig(**({"bootstrap": 200} | (base_wf or {}) | v.wf))
        try:
            out = wf.run(matches, odds, cfg, wcfg)
        except Exception as exc:  # una variante que falla no detiene las demás
            out = {"status": "error", "reason": str(exc)}
        out.pop("_oos", None)
        rows.append(summarize(v, out))
    return rows


def _f(x, nd=4) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def markdown(rows: list[dict], competition: str, n_matches: int) -> str:
    base = next((r for r in rows if r["key"] == "base" and r.get("status") == "ok"), None)
    lines = [f"# Variantes del modelo de fútbol: {competition}", "",
             f"{n_matches} partidos con resultado. Walk-forward: cada bloque se predice solo con partidos "
             "anteriores. Log loss: menor es mejor. Δ vs mercado < 0 significa que la variante fue más precisa "
             "que el mercado sin margen en los partidos con cuotas.", "",
             "| Variante | n | Log loss 1X2 | vs base | Mercado (mismo subconjunto) | Δ vs mercado [IC90] | ECE | "
             "Acierto | O/U 2.5 | Ambos anotan | ROI simulado |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("status") != "ok":
            lines.append(f"| {r['label']} | — | {r.get('reason', r.get('status'))} |  |  |  |  |  |  |  |  |")
            continue
        vs_base = (r["log_loss"] - base["log_loss"]) if base and r["n"] == base["n"] else None
        vm = r.get("vs_market")
        lines.append(
            f"| {r['label']} | {r['n']} | {_f(r['log_loss'])} | {'—' if vs_base is None else f'{vs_base:+.4f}'} | "
            f"{_f(r.get('market_log_loss'))} | "
            f"{'—' if not vm else f'{vm[0]:+.4f} [{vm[1]:+.4f}, {vm[2]:+.4f}]'} | {_f(r['ece'], 3)} | "
            f"{r['accuracy']:.1%} | {_f(r['ou_log_loss'])} | {_f(r['btts_log_loss'])} | "
            f"{'—' if 'roi' not in r else f'{r['roi']:+.1%} ({r['n_bets']})'} |")
    adj = [r for r in rows if r.get("adjustment")]
    if adj:
        lines += ["", "Ajustes estimados con todos los partidos evaluados:"]
        for r in adj:
            a = r["adjustment"]
            lines.append(f"- {r['label']}: factor de empates {a['draw_factor']:.3f}; peso modelo "
                         f"{a['model_weight']:.2f}, peso mercado {a['market_weight']:.2f} "
                         f"({a['n_blend']} partidos con cuotas)")
    return "\n".join(lines)
