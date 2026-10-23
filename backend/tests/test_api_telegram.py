import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import Event, Prediction
from app.reporting.predictions import settle_one
from app.telegram.notifier import DISCLAIMER, TelegramNotifier, format_opportunity, format_summary


@pytest.fixture(scope="module")
def client(db_engine):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_overview_flags_synthetic(client):
    d = client.get("/api/overview").json()
    assert d["data_mode"] == "demo"
    assert d["synthetic_data_present"] is True
    assert d["n_upcoming"] > 0
    assert {p["key"] for p in d["providers"]} >= {"demo", "csv", "the_odds_api", "football_data_org"}
    statuses = {s["key"]: s["status"] for s in d["sports"]}
    assert statuses["nba"] == "disabled" and statuses["football"] == "partial"


def test_events_and_detail(client):
    evs = client.get("/api/events").json()["events"]
    assert evs and all(e["is_synthetic"] for e in evs)
    det = client.get(f"/api/events/{evs[0]['event_id']}").json()
    probs = det["probabilities"]
    assert sum(probs["1x2"].values()) == pytest.approx(1.0)
    assert probs["tail_mass"] < 1e-4
    assert det["limitations"][0].startswith("DATOS SINTÉTICOS")
    assert det["factors"] and det["odds_table"]
    for a in det["assessments"]:
        assert a["category"] in ("candidato_valor", "en_observacion", "ventaja_no_confirmada", "sin_valor",
                                 "datos_insuficientes", "cuota_desactualizada")
    assert client.get("/api/events/999999").status_code == 404


def test_opportunities_ranked_and_explained(client):
    d = client.get("/api/opportunities").json()
    assert "garantía" in d["disclaimer"]
    for r in d["items"]:
        assert r["reasons"]
        assert r["model_status"] in ("production", "experimental", "blocked")
        if r["category"] == "candidato_valor":
            assert r["model_status"] == "production"
            assert r["prob_ev_positive"] >= d["thresholds"]["min_prob_ev_positive"]


def test_thresholds_update_and_validation(client):
    r = client.put("/api/settings/thresholds", json={"changes": {"min_ev": 0.05}})
    assert r.status_code == 200 and r.json()["min_ev"] == 0.05
    assert client.put("/api/settings/thresholds", json={"changes": {"inventado": 1}}).status_code == 400
    assert client.put("/api/settings/thresholds", json={"changes": {"min_odds": 9, "max_odds": 2}}).status_code == 400
    client.put("/api/settings/thresholds", json={"changes": {"min_ev": 0.03}})


def test_csv_upload(client):
    csv = ("Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A\n"
           "SP1,15/08/2025,19:00,Betis,Sociedad,2,2,2.3,3.2,3.1\n")
    r = client.post("/api/imports/csv", files={"file": ("SP1.csv", csv, "text/csv")})
    assert r.status_code == 200 and r.json()["events_created"] == 1
    bad = client.post("/api/imports/csv", files={"file": ("x.txt", "a,b", "text/plain")})
    assert bad.status_code == 400


def test_odds_tool(client):
    d = client.get("/api/tools/odds", params=[("odds", "2.10"), ("odds", "3.40"), ("odds", "3.60"),
                                               ("p_model", "0.52")]).json()
    assert d["overround"] > 0
    assert d["first_selection"]["ev"] == pytest.approx(0.52 * 2.1 - 1)
    assert client.get("/api/tools/odds", params={"odds": "0.5"}).status_code == 400


def test_snapshot_settle_and_performance(client, session):
    snap = client.post("/api/predictions/snapshot").json()
    assert snap["inserted"] > 0
    again = client.post("/api/predictions/snapshot").json()
    assert again["inserted"] == 0  # deduplicado
    perf = client.get("/api/performance").json()
    assert perf["competitions"][0]["walkforward"]["status"] == "ok"
    assert perf["tracked"]["registered"] >= snap["inserted"]


def test_admin_token_required_outside_local(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import require_admin

    with pytest.raises(HTTPException):
        require_admin(None, Settings(app_env="production", admin_token=""))
    with pytest.raises(HTTPException):
        require_admin("malo", Settings(app_env="production", admin_token="bueno"))
    require_admin("bueno", Settings(app_env="production", admin_token="bueno"))


# ------------------------------------------------------------------ liquidación
@pytest.mark.parametrize("market,sel,line,score,state,ret", [
    ("1x2", "home", None, (2, 1), "gana", 1.0),
    ("1x2", "draw", None, (2, 1), "pierde", -1.0),
    ("totals", "over", 2.5, (2, 1), "gana", 1.0),
    ("totals", "under", 3.0, (2, 1), "push", 0.0),
    ("btts", "yes", None, (2, 0), "pierde", -1.0),
    ("ah", "away", 0.25, (1, 1), "medio_gana", 0.5),
    ("ah", "home", -0.75, (2, 1), "medio_gana", 0.5),
])
def test_settlement_rules(market, sel, line, score, state, ret):
    p = Prediction(market=market, selection=sel, line=line, odds=2.0, p_model=0.5)
    ev = Event(home_score=score[0], away_score=score[1])
    s, r, _ = settle_one(p, ev)
    assert s == state and r == pytest.approx(ret)


# ------------------------------------------------------------------ telegram
ROW = {"rank": 1, "sport": "football", "competition": "Liga Demo (SINTÉTICA)", "event": "A vs B",
       "kickoff_local": "2026-10-24 19:00", "label": "Local", "odds": 2.1, "bookmaker": "Operador Demo A",
       "p_model": 0.53, "p_market_fair": 0.47, "fair_odds": 1.89, "ev": 0.113, "ev_ci90": [0.01, 0.2],
       "prob_ev_positive": 0.93, "model_status": "production", "sample_size": 700,
       "odds_captured_at": "2026-10-23T10:00:00+00:00", "category": "candidato_valor", "reasons": ["EV +11%"],
       "risks": ["Varianza alta"], "min_odds": 1.92, "is_synthetic": True}


def test_format_message_contains_required_fields():
    msg = format_opportunity(ROW)
    for text in ("QUANTSPORT AI — RANKING", "Cuota observada: 2.10", "Cuota justa: 1.89", "Decisión del sistema",
                 "Cuota mínima", DISCLAIMER, "SINTÉTICOS"):
        assert text in msg
    assert len(msg) < 4096


def test_summary_never_includes_stale_or_insufficient():
    rows = [ROW | {"category": "cuota_desactualizada"}, ROW | {"category": "datos_insuficientes"}]
    msgs = format_summary(rows, 3)
    assert len(msgs) == 1 and "NO BET" in msgs[0]


def test_telegram_access_control_dedupe_and_limit(session):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200, json={"ok": True})

    st = Settings(telegram_bot_token="123:abc", telegram_allowed_chat_ids="42", telegram_max_messages_per_day=2)
    n = TelegramNotifier(st, transport=httpx.MockTransport(handler))
    assert n.send(session, "999", "hola") == "denied"
    assert n.send(session, "42", "hola", "k1") == "sent"
    assert n.send(session, "42", "hola", "k1") == "duplicate"
    assert n.send(session, "42", "otro", "k2") == "sent"
    assert n.send(session, "42", "tercero", "k3") == "rate_limited"
    assert len(sent) == 2


def test_telegram_not_configured(session):
    res = TelegramNotifier(Settings(telegram_bot_token="")).send_summary(session, [], 0)
    assert res["status"] == "not_configured"


def test_log_redaction():
    from app.core.logging import redact

    assert "secreto" not in redact("GET /v4/sports?apiKey=secreto&regions=eu")
    assert "abc" not in redact("https://api.telegram.org/bot123:abc/sendMessage")
