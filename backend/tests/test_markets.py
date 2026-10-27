"""Módulo universal de cuotas y mercados: formatos, registro, liquidación, evaluación y API.

Los ejemplos numéricos coinciden con docs/CUOTAS_Y_MERCADOS.md.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import ValueThresholds
from app.db.models import Event, OddsSnapshot
from app.markets import registry
from app.markets.evaluate import evaluate_book, margin_info, uncertainty_level
from app.markets.models import FootballMatrixModel, coverage, first_to_score_probs, matrix_distribution
from app.markets.quotes import EventRef, MarketBook, MarketRef, Quote, SelectionRef, stable_id
from app.markets.settlement import Outcome, UnsupportedSettlementError, settle
from app.probability.ev import ev_binary, min_odds_for_target_states
from app.probability.margin import overround, remove_margin
from app.probability.odds import (
    InvalidOddsError,
    american_implied_probability,
    convert_all,
    detect_format,
    fair_odds,
    format_odds,
    implied_probability,
    to_decimal,
)
from app.sports.football.scores import probs_1x2, score_matrix

NOW = datetime.now(UTC)


# ----------------------------------------------------------------- formatos
@pytest.mark.parametrize("value,fmt,expected", [
    ("1.50", "decimal", 1.50), ("2.00", "decimal", 2.00), ("3.25", "decimal", 3.25),
    ("+150", "american", 2.50), ("-200", "american", 1.50), ("+100", "american", 2.00),
    ("3/2", "fractional", 2.50), ("1/2", "fractional", 1.50), ("13/4", "fractional", 4.25),
    ("50%", "percent", 2.00), ("66.67%", "percent", 100 / 66.67), ("33.33%", "percent", 100 / 33.33),
])
def test_formats_to_decimal(value, fmt, expected):
    assert to_decimal(value, fmt) == pytest.approx(expected)
    assert to_decimal(value, "auto") == pytest.approx(expected)  # autodetección


def test_detect_format_and_display():
    assert [detect_format(x) for x in ("2.5", "+150", "-200", "3/2", "40%", "250")] == [
        "decimal", "american", "american", "fractional", "percent", "american"]
    c = convert_all(2.5)
    assert c["display"] == {"decimal": "2.50", "american": "+150", "fractional": "3/2", "percent": "40.00%"}
    assert format_odds(1.5, "american") == "-200" and format_odds(1.5, "fractional") == "1/2"
    assert c["implied_probability"] == pytest.approx(0.40)


def test_implied_probability_formulas():
    assert implied_probability(2.0) == pytest.approx(0.5)
    assert american_implied_probability(150) == pytest.approx(100 / 250)  # 0.40
    assert american_implied_probability(-200) == pytest.approx(200 / 300)  # 0.6667
    # coherente con convertir a decimal primero
    for a in (110, -110, 250, -350):
        assert american_implied_probability(a) == pytest.approx(implied_probability(to_decimal(a, "american")))
    assert fair_odds(0.40) == pytest.approx(2.5)


@pytest.mark.parametrize("value,fmt", [
    ("1.0", "decimal"), ("0.5", "decimal"), ("abc", "auto"), ("nan", "decimal"), ("inf", "auto"),
    ("-50", "american"), ("+99", "american"), ("3/0", "fractional"), ("0/4", "fractional"), ("-1/2", "fractional"),
    ("0%", "percent"), ("100%", "percent"), ("150%", "percent"), ("x%", "percent"),
])
def test_invalid_odds_rejected(value, fmt):
    with pytest.raises((InvalidOddsError, ValueError)):
        to_decimal(value, fmt)


def test_fair_odds_rejects_invalid_probability():
    for p in (0, 1, -0.1, 1.2, float("nan")):
        with pytest.raises(ValueError):
            fair_odds(p)


# ----------------------------------------------------------------- margen y EV
def test_margin_example():
    odds = [2.10, 3.40, 3.60]
    assert overround(odds) == pytest.approx(1 / 2.1 + 1 / 3.4 + 1 / 3.6 - 1)  # 0.0481 = 4.81 %
    assert overround(odds) == pytest.approx(0.04809, abs=1e-5)
    fair = remove_margin(odds, "proportional")
    assert sum(fair) == pytest.approx(1.0) and fair[0] == pytest.approx((1 / 2.1) / 1.04809, abs=1e-5)
    for method in ("power", "shin"):
        assert sum(remove_margin(odds, method)) == pytest.approx(1.0)


def test_ev_binary_example():
    assert ev_binary(0.45, 2.50) == pytest.approx(0.125)  # 0.45 · 2.50 - 1
    assert ev_binary(0.40, 2.50) == pytest.approx(0.0)


def test_ev_with_push_and_partial_settlement():
    # Probabilidades del modelo: local 45 %, empate 28 %, visitante 27 %
    pw, pd_, pl = 0.45, 0.28, 0.27
    # Draw No Bet a 2.00: empate devuelve -> EV = 0.45·1 + 0.28·0 - 0.27 = 0.18
    dnb = [settle("handicap", "home", 0.0, 2.0, Outcome(h, a)) for h, a in ((1, 0), (1, 1), (0, 1))]
    assert [s for s, _ in dnb] == ["gana", "push", "pierde"]
    assert sum(p * r for p, (_, r) in zip((pw, pd_, pl), dnb, strict=True)) == pytest.approx(0.18)
    # Hándicap asiático -0.25 a 1.90: empate = medio pierde (-0.5) -> EV = 0.45·0.9 - 0.28·0.5 - 0.27 = -0.005
    ah = [settle("handicap", "home", -0.25, 1.9, Outcome(h, a)) for h, a in ((1, 0), (1, 1), (0, 1))]
    assert [s for s, _ in ah] == ["gana", "medio_pierde", "pierde"]
    assert sum(p * r for p, (_, r) in zip((pw, pd_, pl), ah, strict=True)) == pytest.approx(-0.005)
    # Total asiático 2.25 over a 1.90 con 3 goles gana completo, con 2 medio pierde
    assert settle("total", "over", 2.25, 1.9, Outcome(2, 1)) == ("gana", pytest.approx(0.9))
    assert settle("total", "over", 2.25, 1.9, Outcome(1, 1)) == ("medio_pierde", -0.5)
    assert settle("total", "under", 2.25, 2.0, Outcome(1, 1)) == ("medio_gana", 0.5)
    assert settle("total", "under", 2.75, 2.0, Outcome(2, 1)) == ("medio_pierde", -0.5)
    # Cuota justa con push: la que deja EV = 0 -> 1 + 0.27/0.45 = 1.6
    assert min_odds_for_target_states(pw, pd_, 0.0) == pytest.approx(1.6)


def test_settlement_rules_per_sport():
    assert settle("winner_3way", "draw", -1.0, 4.0, Outcome(2, 1))[0] == "gana"  # hándicap europeo
    assert settle("double_chance", "X2", None, 1.5, Outcome(0, 0))[0] == "gana"
    assert settle("correct_score", "2-1", None, 8.0, Outcome(2, 1)) == ("gana", 7.0)
    assert settle("team_total", "away_under", 1.5, 1.8, Outcome(3, 1))[0] == "gana"
    assert settle("ht_ft", "X/1", None, 5.0, Outcome(2, 1, extra={"ht_home": 1, "ht_away": 1}))[0] == "gana"
    assert settle("winner_2way", "home", None, 1.9, Outcome(20, 20)) == ("push", 0.0)  # empate NFL
    assert settle("handicap", "away", 4.5, 1.91, Outcome(110, 106))[0] == "gana"  # spread NBA +4.5
    assert settle("handicap", "home", -3.0, 1.91, Outcome(24, 21)) == ("push", 0.0)  # spread entero
    assert settle("total", "over", 8.5, 1.9, Outcome(5, 4))[0] == "gana"  # carreras MLB
    assert settle("player_total", "over", 24.5, 1.9, Outcome(value=27))[0] == "gana"
    assert settle("yes_no", "yes", None, 3.0, Outcome(value=1))[0] == "gana"  # doble-doble
    assert settle("total", "over", 22.5, 1.9, Outcome(value=24))[0] == "gana"  # total de juegos (tenis)


def test_settlement_missing_data():
    with pytest.raises(UnsupportedSettlementError):
        settle("winner_3way", "home", None, 2.0, Outcome())
    with pytest.raises(UnsupportedSettlementError):
        settle("ht_ft", "1/1", None, 3.0, Outcome(1, 0))  # falta el descanso
    with pytest.raises(UnsupportedSettlementError):
        settle("first_to_score", "home", None, 2.0, Outcome(1, 0))
    with pytest.raises(UnsupportedSettlementError):
        settle("no_existe", "home", None, 2.0, Outcome(1, 0))
    # jugador que no participa: anulada
    assert settle("player_total", "over", 0.5, 2.0, Outcome()) == ("push", 0.0)


# ----------------------------------------------------------------- registro
def test_registry_covers_requested_markets():
    keys = {sp: {m.key for m in registry.for_sport(sp)} for sp in registry.sports()}
    assert {"1x2", "double_chance", "dnb", "european_handicap", "ah", "totals", "btts", "correct_score", "ht_result",
            "ht_ft", "team_totals", "corners_totals", "cards_totals", "first_team_to_score", "next_goal"} <= keys["football"]
    assert {"moneyline", "spread", "totals", "team_totals", "period_result", "ht_winner", "player_points",
            "player_rebounds", "player_assists", "player_steals", "player_blocks", "player_threes", "player_pra",
            "player_double_double", "player_triple_double"} <= keys["nba"]
    assert {"moneyline", "spread", "totals", "team_totals", "ht_winner", "player_passing_yards",
            "player_rushing_yards", "player_receiving_yards", "player_touchdowns", "player_interceptions",
            "total_touchdowns", "period_spread", "period_totals"} <= keys["nfl"]
    assert {"moneyline", "run_line", "totals", "team_totals", "first5_moneyline", "player_pitcher_strikeouts",
            "player_pitcher_outs", "player_pitcher_earned_runs", "player_batter_hits", "player_batter_total_bases",
            "player_batter_rbis", "player_batter_home_runs"} <= keys["mlb"]
    assert {"match_winner", "set_winner", "set_handicap", "game_handicap", "total_games", "total_sets",
            "first_set_winner", "correct_score_sets", "player_aces", "player_double_faults"} <= keys["tennis"]


@pytest.mark.parametrize("args,ok", [
    (("football", "ah", "home", -0.75), True),
    (("football", "ah", "home", -0.3), False),            # no es múltiplo de 0.25
    (("football", "totals", "over", None), False),        # falta la línea
    (("football", "totals", "over", -1.5), False),        # total negativo
    (("football", "1x2", "over"), False),                 # selección de otro mercado
    (("football", "1x2", "home", 1.0), False),            # 1X2 no lleva línea
    (("football", "european_handicap", "draw", -1.0), True),
    (("football", "european_handicap", "draw", -0.5), False),
    (("football", "correct_score", "2-1"), True),
    (("football", "correct_score", "dos-uno"), False),
    (("nba", "player_points", "over", 24.5), False),      # falta el jugador
    (("nba", "player_points", "over", 24.5, None, "Jugador A"), True),
    (("nba", "moneyline", "home", None, None, "Jugador A"), False),
    (("tennis", "set_winner", "home", None, "set3"), True),
    (("tennis", "set_winner", "home", None, "set6"), False),
    (("football", "corners", "over", 9.5), False),        # mercado no registrado
])
def test_registry_validation(args, ok):
    if ok:
        assert registry.validate(*args).selection == args[2]
    else:
        with pytest.raises(registry.MarketValidationError):
            registry.validate(*args)


def test_model_coverage_is_honest():
    cov = coverage()
    assert cov["football"]["1x2"] is None and cov["football"]["correct_score"] is None
    assert "córners" in cov["football"]["corners_totals"]
    assert "vivo" in cov["football"]["next_goal"]
    for m in ("moneyline", "spread", "totals", "team_totals"):
        assert cov["nba"][m] is None
    assert all(r and "Sin modelo" in r for k, r in cov["nba"].items()
               if k not in ("moneyline", "spread", "totals", "team_totals"))
    for sport in ("nfl", "mlb", "tennis"):
        assert all(reason and "Sin modelo" in reason for reason in cov[sport].values())


# ----------------------------------------------------------------- modelo y evaluador
SM = score_matrix(1.5, 1.1, -0.05)
SAMPLED = [score_matrix(1.45, 1.15), score_matrix(1.55, 1.05), score_matrix(1.5, 1.1)]


def test_matrix_distribution_matches_closed_forms():
    p = probs_1x2(SM)
    d = matrix_distribution(SM, "winner_3way", "home", None, 2.0)
    assert d.p_full_win == pytest.approx(p["home"])
    dc = matrix_distribution(SM, "double_chance", "1X", None, 1.3)
    assert dc.p_full_win == pytest.approx(p["home"] + p["draw"])
    dnb = matrix_distribution(SM, "handicap", "home", 0.0, 2.0)
    assert dnb.p_push == pytest.approx(p["draw"])
    assert dnb.expected_value == pytest.approx(p["home"] * 1.0 - p["away"])
    cs = sum(matrix_distribution(SM, "correct_score", f"{h}-{a}", None, 2.0).p_full_win
             for h in range(11) for a in range(11))
    assert cs == pytest.approx(1.0)
    f = first_to_score_probs(SM)
    assert sum(f.values()) == pytest.approx(1.0) and f["none"] == pytest.approx(SM.matrix[0, 0] / SM.matrix.sum())


def _book(market: str, sels: list[tuple[str, float]], line: float | None = None, sport: str = "football",
          age_min: float = 5, status: str = "open") -> MarketBook:
    ev = EventRef(1, "e", sport, "x", "Liga X", "Local FC", "Visita FC", NOW + timedelta(days=1), "scheduled", False)
    mdef = registry.get(sport, market)
    mref = MarketRef("m", "e", market, mdef.label, "ft", line, None, False, status, mdef.settlement_note)
    when = NOW - timedelta(minutes=age_min)
    return MarketBook(ev, mref, mdef, "Fuente", [
        (SelectionRef(f"s{i}", "m", s, s), Quote(f"q{i}", f"s{i}", "Fuente", o, 1 / o, "decimal", str(o), "manual",
                                                 when, False)) for i, (s, o) in enumerate(sels)])


TH = ValueThresholds()


def test_evaluate_full_1x2_market():
    fm = FootballMatrixModel(SM, SAMPLED)
    res = evaluate_book(_book("1x2", [("home", 2.10), ("draw", 3.40), ("away", 3.60)]), fm, None, TH, now=NOW)
    assert res["margin"]["available"] and res["margin"]["overround"] == pytest.approx(0.04809, abs=1e-5)
    home = res["selections"][0]
    p = probs_1x2(SM)["home"]
    assert home["p_model"] == pytest.approx(p) and home["fair_odds"] == pytest.approx(1 / p)
    assert home["ev"] == pytest.approx(p * 2.10 - 1)
    assert home["edge"] == pytest.approx(p - home["p_no_vig"])
    assert home["display"]["american"] == "+110" and home["uncertainty"] in ("baja", "media", "alta")
    assert home["model_status"] == "experimental"  # sin control de calidad: nunca candidato


def test_evaluate_push_market_has_no_edge_and_uses_states():
    fm = FootballMatrixModel(SM, SAMPLED)
    res = evaluate_book(_book("ah", [("home", 1.95), ("away", 1.95)], line=-0.25), fm, None, TH, now=NOW)
    row = res["selections"][0]
    assert row["p_no_vig"] is not None and row["edge"] is None
    assert any("devolución" in r for r in row["risks"])
    assert row["ev"] == pytest.approx(matrix_distribution(SM, "handicap", "home", -0.25, 1.95).expected_value)


def test_evaluate_partial_and_non_exclusive_markets():
    fm = FootballMatrixModel(SM, SAMPLED)
    one = evaluate_book(_book("1x2", [("home", 2.10)]), fm, None, TH, now=NOW)
    assert not one["margin"]["available"] and "Faltan" in one["margin"]["note"]
    assert one["selections"][0]["p_no_vig"] is None and one["selections"][0]["ev"] is not None
    dc = evaluate_book(_book("double_chance", [("1X", 1.25), ("X2", 1.6), ("12", 1.3)]), fm, None, TH, now=NOW)
    assert not dc["margin"]["available"] and "excluyentes" in dc["margin"]["note"]


def test_evaluate_without_model_never_invents():
    res = evaluate_book(_book("moneyline", [("home", 1.8), ("away", 2.1)], sport="nba"), None,
                        "Sin modelo de NBA todavía", TH, now=NOW)
    for row in res["selections"]:
        assert row["p_model"] is None and row["ev"] is None and row["category"] == "datos_insuficientes"
        assert row["implied_probability"] > 0 and row["p_no_vig"] is not None
    fm = FootballMatrixModel(SM, SAMPLED)
    corners = evaluate_book(_book("corners_totals", [("over", 1.9), ("under", 1.9)], line=9.5), fm, None, TH, now=NOW)
    assert corners["selections"][0]["ev"] is None and "córners" in corners["model_note"]
    suspended = evaluate_book(_book("1x2", [("home", 2.1)], status="suspended"), fm, None, TH, now=NOW)
    assert suspended["selections"][0]["ev"] is None


def test_high_odds_alone_is_not_value():
    fm = FootballMatrixModel(SM, SAMPLED)
    res = evaluate_book(_book("correct_score", [("4-3", 60.0)]), fm, None, TH, now=NOW)
    row = res["selections"][0]
    assert row["ev"] < 0 and row["category"] == "sin_valor"


def test_stale_odds_flagged():
    fm = FootballMatrixModel(SM, SAMPLED)
    res = evaluate_book(_book("btts", [("yes", 1.8), ("no", 2.0)], age_min=TH.max_odds_age_minutes + 30), fm, None,
                        TH, now=NOW)
    assert res["selections"][0]["data_quality"]["stale"] and res["selections"][0]["category"] == "cuota_desactualizada"


def test_margin_method_configurable_and_validated():
    b = _book("1x2", [("home", 1.5), ("draw", 4.2), ("away", 7.0)])
    prop, _ = margin_info(b, "proportional")
    shin, info = margin_info(b, "shin")
    assert info["method"] == "shin" and prop["away"] != pytest.approx(shin["away"])
    with pytest.raises(ValueError):
        margin_info(b, "inventado")


def test_uncertainty_levels():
    assert uncertainty_level([0.40, 0.44], None) == "baja"
    assert uncertainty_level([0.30, 0.50], None) == "alta"
    assert uncertainty_level(None, [-0.05, 0.10]) == "media"
    assert uncertainty_level(None, None) == "desconocida"


def test_stable_ids():
    a = stable_id("football", "mex", "2026-10-24T02:00", "america", "toluca")
    assert a == stable_id("football", "mex", "2026-10-24T02:00", "america", "toluca") and len(a) == 16
    assert a != stable_id("football", "mex", "2026-10-24T02:00", "toluca", "america")
    assert stable_id("m", 2.5) == stable_id("m", 2.50) != stable_id("m", 2.25)


# ----------------------------------------------------------------- API
@pytest.fixture()
def client(db_engine, demo_loaded):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_registry_and_convert_endpoints(client):
    reg = client.get("/api/markets/registry").json()
    assert {s["key"] for s in reg["sports"]} == {"football", "nba", "nfl", "mlb", "tennis"}
    assert reg["formats"] == ["decimal", "american", "fractional", "percent"]
    nba = client.get("/api/markets/registry", params={"sport": "nba"}).json()["sports"][0]
    modeled = {m["key"] for m in nba["markets"] if m["has_model"]}
    assert modeled == {"moneyline", "spread", "totals", "team_totals"}
    assert client.get("/api/markets/registry", params={"sport": "curling"}).status_code == 404

    r = client.get("/api/tools/convert", params={"value": "+150"}).json()
    assert r["decimal"] == pytest.approx(2.5) and r["format_detected"] == "american" and r["display"]["fractional"] == "3/2"
    r = client.get("/api/tools/convert", params={"value": "2.50", "p": 0.45}).json()
    assert r["evaluation"]["ev"] == pytest.approx(0.125) and r["evaluation"]["fair_odds"] == pytest.approx(1 / 0.45)
    r = client.get("/api/tools/convert", params={"value": "2.0", "p": 0.45, "p_push": 0.28}).json()
    assert r["evaluation"]["ev"] == pytest.approx(0.18) and r["evaluation"]["fair_odds"] == pytest.approx(1.6)
    assert client.get("/api/tools/convert", params={"value": "3/0"}).status_code == 400
    assert client.get("/api/tools/convert", params={"value": "2.0", "p": 0.8, "p_push": 0.3}).status_code == 400


def test_board_endpoint_filters(client, session):
    d = client.get("/api/markets/board", params={"sport": "football", "days": 30}).json()
    assert d["markets"], "el demo debe tener mercados con cuotas"
    m = d["markets"][0]
    assert {"event", "market", "bookmaker", "margin", "selections"} <= set(m)
    row = m["selections"][0]
    assert {"implied_probability", "p_no_vig", "p_model", "fair_odds", "edge", "ev", "uncertainty",
            "data_quality", "display"} <= set(row)
    f = client.get("/api/markets/board", params={"days": 30, "min_odds": 2.0, "max_odds": 3.0}).json()
    assert all(2.0 <= r["quote"]["odds_decimal"] <= 3.0 for mk in f["markets"] for r in mk["selections"])
    f = client.get("/api/markets/board", params={"days": 30, "min_prob": 0.5}).json()
    assert all(r["implied_probability"] >= 0.5 for mk in f["markets"] for r in mk["selections"])
    f = client.get("/api/markets/board", params={"days": 30, "min_ev": 0.0}).json()
    assert all(r["ev"] is not None and r["ev"] >= 0 for mk in f["markets"] for r in mk["selections"])
    f = client.get("/api/markets/board", params={"days": 30, "market": "btts", "margin_method": "shin"}).json()
    assert all(mk["market"]["key"] == "btts" for mk in f["markets"]) and f["margin_method"] == "shin"
    assert client.get("/api/markets/board", params={"margin_method": "otro"}).status_code == 422
    assert client.get("/api/markets/board", params={"event_id": 99999999}).status_code == 404


def test_extra_markets_reach_event_analysis_and_ranking(client, session):
    ev = session.scalar(select(Event).where(Event.status == "scheduled", Event.kickoff_utc > NOW)
                        .order_by(Event.kickoff_utc))
    url = f"/api/events/{ev.id}/manual-odds"
    assert client.post(url, json={"bookmaker": "Fuente Z", "market": "dnb", "selection": "home",
                                  "odds": "1.80"}).status_code == 200
    assert client.post(url, json={"bookmaker": "Fuente Z", "market": "team_totals", "selection": "home_over",
                                  "line": 1.5, "odds": "2.30"}).status_code == 200
    an = client.get(f"/api/events/{ev.id}").json()
    markets = {a["market"] for a in an["assessments"]}
    assert {"dnb", "team_totals"} <= markets
    dnb = next(a for a in an["assessments"] if a["market"] == "dnb")
    assert dnb["p_push"] is not None and dnb["edge"] is None
    snap = session.scalar(select(OddsSnapshot).where(OddsSnapshot.event_id == ev.id, OddsSnapshot.market == "dnb"))
    assert snap.original_format == "decimal"
    board = client.get("/api/markets/board", params={"event_id": ev.id, "market": "team_totals"}).json()
    assert board["markets"][0]["selections"][0]["selection"]["label"].endswith("1.5")


def test_quality_score_uses_sample_and_availability():
    from app.value_engine.engine import Assessment, _quality_score

    a = Assessment("1x2", "home", None, "x", "candidato_valor", "", odds_age_minutes=0, ev=0.05,
                   prob_ev_positive=0.8, robustness=0.02, n_offers=1)
    low = _quality_score(a, 0.8, TH, sample_score=0.1)
    high = _quality_score(a, 0.8, TH, sample_score=1.0)
    a.n_offers = 3
    more = _quality_score(a, 0.8, TH, sample_score=1.0)
    assert low < high < more
    assert np.isclose(more - high, 0.10 * (1 - 1 / 3), atol=1e-4)
