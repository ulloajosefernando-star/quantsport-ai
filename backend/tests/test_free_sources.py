"""Fuentes gratuitas adicionales: openfootball (dominio público) y Club Elo (CSV importado a mano).

HTTP simulado con httpx.MockTransport; datos construidos en código.
"""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Event, Team, TeamRating
from app.ingestion import teams
from app.ingestion.jobs import import_csv
from app.ingestion.store import Ingestor
from app.providers import clubelo
from app.providers.base import NormalizedEvent, ProviderBatch, ProviderError
from app.providers.openfootball import OpenFootballProvider, parse_matches, season_label

NOW = datetime.now(UTC).replace(microsecond=0)


def test_season_label():
    assert season_label("2025") == "2025-26"
    assert season_label("2025/26") == "2025-26"
    assert season_label("1999") == "1999-00"
    assert season_label(None, today=date(2026, 10, 23)) == "2026-27"
    assert season_label(None, today=date(2026, 3, 1)) == "2025-26"


def test_parse_openfootball_formats():
    data = {"name": "Spain Primera División 2025/26", "matches": [
        {"round": "Matchday 1", "date": "2025-08-15", "time": "21:30", "team1": "Real Betis Balompié",
         "team2": "Girona FC", "score": {"ft": [1, 0], "ht": [1, 0]}},
        {"round": "Matchday 1", "date": "2025-08-16", "time": "19:00", "team1": "Villarreal CF",
         "team2": "Real Oviedo", "score": [2, 0]},
        {"round": "Matchday 30", "date": "2026-04-04", "team1": "FC Barcelona", "team2": "Sevilla FC"},
        {"round": "x", "date": "no-es-fecha", "team1": "A", "team2": "B"},
    ]}
    evs = parse_matches(data, "esp_la_liga", "2025-26")
    assert len(evs) == 3
    a, b, c = evs
    assert (a.home, a.home_score, a.away_score, a.home_score_ht, a.status) == ("Betis", 1, 0, 1, "finished")
    assert a.kickoff_utc == datetime(2025, 8, 15, 19, 30, tzinfo=UTC)  # 21:30 en Madrid (verano) = 19:30 UTC
    assert (b.home_score, b.away_score, b.home_score_ht) == (2, 0, None)
    assert c.status == "scheduled" and c.home_score is None and c.kickoff_utc.hour == 10  # mediodía local
    assert all(e.source == "openfootball" and e.season == "2025-26" for e in evs)


def test_openfootball_provider_requests_raw_file(tmp_path, db_engine):
    seen = []
    future = (NOW + timedelta(days=3)).date().isoformat()

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, json={"name": "x", "matches": [
            {"date": "2025-08-15", "time": "20:00", "team1": "Liverpool FC", "team2": "AFC Bournemouth",
             "score": {"ft": [4, 2], "ht": [1, 0]}},
            {"date": future, "time": "15:00", "team1": "Arsenal FC", "team2": "Chelsea FC"},
        ]})

    p = OpenFootballProvider(Settings(cache_dir=tmp_path), transport=httpx.MockTransport(handler))
    res = p.fetch_results("eng_premier_league", "2025")
    assert seen == ["https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/en.1.json"]
    assert [(e.home, e.home_score) for e in res.events] == [("Liverpool FC", 4)]
    fx = p.fetch_fixtures("eng_premier_league", days_ahead=7, season="2025")
    assert [(e.home, e.status) for e in fx.events] == [("Arsenal FC", "scheduled")]
    assert len(seen) == 1  # la segunda lectura sale de la caché
    with pytest.raises(ProviderError, match="openfootball"):
        p.fetch_results("mex_liga_mx")


def test_openfootball_cross_check_flags_discrepancy(session):
    k = datetime(2024, 9, 1, 14, tzinfo=UTC)
    first = NormalizedEvent(sport="football", competition_key="test_cruce", competition_name="Cruce",
                            kickoff_utc=k, home="Cruce Uno", away="Cruce Dos", source="csv:x", status="finished",
                            home_score=2, away_score=1)
    second = NormalizedEvent(sport="football", competition_key="test_cruce", competition_name="Cruce",
                             kickoff_utc=k + timedelta(hours=1), home="Cruce Uno", away="Cruce Dos",
                             source="openfootball", status="finished", home_score=2, away_score=2)
    Ingestor(session).ingest(ProviderBatch(source="csv:x", fetched_at=NOW, events=[first]))
    rep = Ingestor(session).ingest(ProviderBatch(source="openfootball", fetched_at=NOW, events=[second]))
    assert any("discrepante" in w for w in rep.warnings)
    assert rep.events_created == 0


CLUBELO_CSV = """Rank,Club,Country,Level,Elo,From,To
1,ManCity,ENG,1,2050.1,2026-10-01,2026-10-20
1,ManCity,ENG,1,2061.4,2026-10-21,2026-10-27
2,RealMadrid,ESP,1,2001.0,2026-10-15,2026-10-27
None,Inventado FC,XXX,1,1500,2026-10-01,2026-10-27
"""


def test_clubelo_import_and_lookup(session):
    assert clubelo.split_camel("RealMadrid") == "Real Madrid" and clubelo.split_camel("Man City") == "Man City"
    Ingestor(session).ingest(ProviderBatch(source="csv:x", fetched_at=NOW, events=[
        NormalizedEvent(sport="football", competition_key="eng_premier_league", competition_name="Premier League",
                        kickoff_utc=datetime(2026, 10, 25, 15, tzinfo=UTC), home="Manchester City FC",
                        away="Real Madrid", source="csv:x")]))
    rep = clubelo.import_ratings(session, CLUBELO_CSV)
    assert rep["ratings_inserted"] == 3 and rep["unmatched_clubs"] == 1
    assert clubelo.import_ratings(session, CLUBELO_CSV)["ratings_duplicates"] == 3
    city = session.scalar(select(Team).where(Team.canonical_key == "manchester_city"))
    assert clubelo.rating_at(session, city.id, datetime(2026, 10, 25, tzinfo=UTC)) == pytest.approx(2061.4)
    assert clubelo.rating_at(session, city.id, datetime(2026, 10, 10, tzinfo=UTC)) == pytest.approx(2050.1)
    assert clubelo.rating_at(session, city.id, datetime(2026, 9, 1, tzinfo=UTC)) is None


def test_clubelo_routed_from_csv_import_and_rejects_bad_files(session):
    with pytest.raises(clubelo.ClubEloFormatError):
        clubelo.parse_csv("a,b\n1,2\n")
    rep = import_csv(session, CLUBELO_CSV.encode(), "elo.csv")
    assert rep["format"] == "clubelo"


def test_merge_moves_ratings(session):
    Ingestor(session).ingest(ProviderBatch(source="csv:x", fetched_at=NOW, events=[
        NormalizedEvent(sport="football", competition_key="test_rating", competition_name="R",
                        kickoff_utc=NOW + timedelta(days=1), home="Rating Alfa", away="Rating Beta", source="csv:x"),
        NormalizedEvent(sport="football", competition_key="test_rating", competition_name="R",
                        kickoff_utc=NOW + timedelta(days=8), home="Rating A.", away="Rating Beta", source="api")]))
    src = session.scalar(select(Team).where(Team.name == "Rating A."))
    dst = session.scalar(select(Team).where(Team.name == "Rating Alfa"))
    session.add(TeamRating(team_id=src.id, source="clubelo", rating=1700.0, valid_from=NOW - timedelta(days=2)))
    session.flush()
    teams.merge(session, src.id, dst.id)
    assert session.scalar(select(TeamRating).where(TeamRating.team_id == dst.id)).rating == 1700.0
    assert len(session.scalars(select(Event).where(Event.home_team_id == dst.id)).all()) == 2


def test_event_detail_shows_elo_reference(demo_loaded, session):
    from app.core.runtime_settings import get_thresholds
    from app.sports.football.service import football_service

    ev = session.scalar(select(Event).where(Event.is_synthetic.is_(True), Event.status == "scheduled")
                        .order_by(Event.kickoff_utc))
    when = ev.kickoff_utc.replace(tzinfo=UTC) - timedelta(days=5)
    session.add_all([TeamRating(team_id=ev.home_team_id, source="clubelo", rating=1800.0, valid_from=when),
                     TeamRating(team_id=ev.away_team_id, source="clubelo", rating=1600.0, valid_from=when)])
    session.flush()
    out = football_service.analyze_event(session, ev, get_thresholds(session))
    ext = out["external_ratings"]
    assert ext["diff"] == 200.0 and ext["expectancy_neutral"] == pytest.approx(1 / (1 + 10 ** (-0.5)))
    if out["probabilities"]:
        assert 0 <= ext["model_expectancy"] <= 1 and isinstance(ext["discrepancy"], bool)
