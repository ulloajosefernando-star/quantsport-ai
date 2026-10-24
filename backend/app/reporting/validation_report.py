"""Informe de validación en Markdown (para la terminal y para guardar en data/reports/)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.calibration.quality_gate import BLOCKED, PRODUCTION
from app.sports.football.service import CompetitionState

STATUS_ES = {PRODUCTION: "VALIDADO", BLOCKED: "BLOQUEADO", "experimental": "EXPERIMENTAL", "sin_modelo": "SIN MODELO"}
MARKET_ES = {"1x2": "1X2", "totals_2.5": "Over/Under 2.5", "btts": "Ambos anotan"}


def _f(x, nd: int = 4) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def build_markdown(st: CompetitionState) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Validación · {st.competition_name}", "",
             f"Generado: {now}. Datos {'SINTÉTICOS' if st.is_synthetic else 'reales'}.", ""]
    wf = st.walkforward or {}
    if st.model is None:
        lines += [f"Sin modelo: {st.error or 'datos insuficientes'}.", ""]
        return "\n".join(lines)
    lines += [f"Partidos usados para entrenar: {st.model.n_matches} · último resultado: "
              f"{st.last_result_at:%Y-%m-%d}" if st.last_result_at else "", ""]
    if wf.get("status") != "ok":
        lines += [f"Validación no disponible: {wf.get('reason', 'sin detalle')}.", ""]
        return "\n".join(lines)
    ref = wf.get("market_reference", {})
    lines += [
        f"Walk-forward: {wf['n_eval']} predicciones fuera de muestra, {wf['refits']} reentrenamientos, "
        f"periodo de prueba {wf['test_period'][0][:10]} → {wf['test_period'][1][:10]}.",
        f"Control de fuga: {wf['leakage_check']}.",
        f"Referencia de mercado: {ref.get('pre_cierre', 0)} partidos con pre-cierre, {ref.get('cierre', 0)} solo con "
        f"cierre, {ref.get('sin_cuotas', 0)} sin cuotas.", "",
        "## Estado por mercado", "",
        "| Mercado | Estado | Brier modelo | Log loss modelo | Log loss frecuencias | Log loss mercado | ECE |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, label in MARKET_ES.items():
        sec = wf.get(key)
        if not sec:
            continue
        status = st.gate.market_status(key if key != "totals_2.5" else "totals", 2.5 if key == "totals_2.5" else None) \
            if st.gate else "experimental"
        mk = sec.get("baseline_mercado") or {}
        lines.append(f"| {label} | {STATUS_ES.get(status, status)} | {_f(sec['model']['brier'])} | "
                     f"{_f(sec['model']['log_loss'])} | {_f(sec['baseline_frecuencias']['log_loss'])} | "
                     f"{_f(mk.get('log_loss'))} | {_f(sec['model']['ece'], 3)} |")
    lines += ["", "## Controles de calidad", ""]
    for c in (st.gate.checks if st.gate else []):
        lines.append(f"- {'✔' if c['passed'] else '✘'} {MARKET_ES.get(c['market'], c['market'])} · {c['check']}: "
                     f"{c['detail']}")
    b = wf.get("betting") or {}
    lines += ["", "## Simulación económica (stake plano de 1 unidad)", ""]
    if b.get("n_bets"):
        ci = b.get("roi_ci90") or [None, None]
        lines += [
            f"- Apuestas simuladas: {b['n_bets']} ({b.get('wins', 0)} ganadas, {b.get('losses', 0)} perdidas)",
            f"- ROI: {_pct(b.get('roi'))} (IC90 {_pct(ci[0])} a {_pct(ci[1])})",
            f"- Drawdown máximo: {_f(b.get('max_drawdown_units'), 1)} unidades",
            f"- CLV medio: {_pct(b.get('clv_mean'))}",
        ]
        for mkt, v in (b.get("by_market") or {}).items():
            lines.append(f"- {MARKET_ES.get(mkt, mkt)}: {v['n']} apuestas, ROI {_pct(v['roi'])}")
    else:
        lines.append("- Sin apuestas simuladas.")
    if b.get("note"):
        lines.append(f"- Nota: {b['note']}")
    lines += ["", "Un ROI histórico positivo no demuestra rentabilidad futura. Análisis probabilístico, no garantía "
              "de resultado.", ""]
    return "\n".join(lines)
