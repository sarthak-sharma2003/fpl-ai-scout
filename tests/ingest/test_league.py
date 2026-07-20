from __future__ import annotations

import httpx
import pytest
import respx

from fplscout import db
from fplscout.ingest.fpl_api import BASE_URL, FplApiClient
from fplscout.ingest.league import sync_league

SEASON = "2026-27"
LEAGUE_ID = 987654


@pytest.fixture
def con():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def client(tmp_path):
    c = FplApiClient(cache_dir=tmp_path / "raw", min_interval=0.0)
    yield c
    c.close()


def _mock_standings(load_fixture):
    return respx.get(f"{BASE_URL}/leagues-classic/{LEAGUE_ID}/standings/").mock(
        return_value=httpx.Response(200, json=load_fixture("leagues_classic_standings.json"))
    )


@respx.mock
def test_sync_pre_season_writes_standings_only(con, client, load_fixture):
    _mock_standings(load_fixture)
    summary = sync_league(con, client, LEAGUE_ID, SEASON, element_to_code={})
    assert summary == {"entries": 3, "gw_rows": 0, "pick_rows": 0}
    top = con.execute(
        "SELECT entry_name, league_name FROM league_standings ORDER BY rank LIMIT 1"
    ).fetchone()
    assert top == ("Embeddings FC", "AI Overlords XI")


@respx.mock
def test_sync_ingests_history_picks_chips_and_maps_codes(con, client, load_fixture):
    con.execute(
        "INSERT INTO gameweeks (season, event, finished) VALUES (?, 1, true)", [SEASON]
    )
    _mock_standings(load_fixture)
    history = load_fixture("entry_history.json")
    history["current"] = history["current"][:1]  # one played gw
    history["chips"] = [{"name": "wildcard", "time": "2026-08-16T10:00:00Z", "event": 1}]
    respx.get(url__regex=rf"{BASE_URL}/entry/\d+/history/").mock(
        return_value=httpx.Response(200, json=history)
    )
    picks = load_fixture("entry_picks.json")
    respx.get(url__regex=rf"{BASE_URL}/entry/\d+/event/1/picks/").mock(
        return_value=httpx.Response(200, json=picks)
    )

    summary = sync_league(con, client, LEAGUE_ID, SEASON, element_to_code={287: 99287})
    assert summary == {"entries": 3, "gw_rows": 3, "pick_rows": 45}
    assert con.execute(
        "SELECT DISTINCT active_chip FROM rival_gw WHERE gw = 1"
    ).fetchall() == [("wildcard",)]
    # element 287 mapped through element_to_code; unmapped elements stay NULL
    assert con.execute(
        "SELECT DISTINCT code FROM rival_picks WHERE element_id = 287"
    ).fetchone()[0] == 99287

    # re-sync is idempotent and fetches no new picks (already have gw 1)
    summary2 = sync_league(con, client, LEAGUE_ID, SEASON, element_to_code={287: 99287})
    assert summary2["pick_rows"] == 0
    assert con.execute("SELECT COUNT(*) FROM rival_picks").fetchone()[0] == 45


@respx.mock
def test_sync_skips_entries_with_404_picks(con, client, load_fixture):
    con.execute(
        "INSERT INTO gameweeks (season, event, finished) VALUES (?, 1, true)", [SEASON]
    )
    _mock_standings(load_fixture)
    history = load_fixture("entry_history.json")
    history["current"] = history["current"][:1]
    respx.get(url__regex=rf"{BASE_URL}/entry/\d+/history/").mock(
        return_value=httpx.Response(200, json=history)
    )
    respx.get(url__regex=rf"{BASE_URL}/entry/\d+/event/1/picks/").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )

    summary = sync_league(con, client, LEAGUE_ID, SEASON, element_to_code={})
    assert summary["pick_rows"] == 0  # skipped, not fatal
    assert summary["gw_rows"] == 3
