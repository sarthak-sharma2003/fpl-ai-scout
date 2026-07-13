from __future__ import annotations

from pathlib import Path

import httpx
import respx

from fplscout import db
from fplscout.features.build import write_features
from fplscout.ingest import live_gw, vaastav
from fplscout.ingest.fpl_api import BASE_URL, FplApiClient
from fplscout.ingest.schemas import BootstrapStatic

FIXTURES_DIR_VAASTAV = Path(__file__).parent.parent / "fixtures" / "vaastav" / "2021-22"


def test_derive_current_season_from_earliest_event_deadline(load_fixture):
    bootstrap = BootstrapStatic.model_validate(load_fixture("bootstrap_static.json"))
    assert live_gw.derive_current_season(bootstrap) == "2025-26"


def _empty_summary() -> dict:
    return {"fixtures": [], "history": [], "history_past": []}


def _one_history_row() -> dict:
    return {
        "element": 1,
        "fixture": 1,
        "opponent_team": 4,
        "total_points": 6,
        "was_home": True,
        "kickoff_time": "2025-08-15T19:00:00Z",
        "round": 1,
        "modified": True,
        "minutes": 90,
        "goals_scored": 0,
        "assists": 0,
        "clean_sheets": 1,
        "goals_conceded": 0,
        "own_goals": 0,
        "penalties_saved": 0,
        "penalties_missed": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "saves": 3,
        "bonus": 2,
        "bps": 30,
        "influence": "40.0",
        "creativity": "0.0",
        "threat": "0.0",
        "ict_index": "4.0",
        "starts": 1,
        "expected_goals": "0.00",
        "expected_assists": "0.00",
        "expected_goal_involvements": "0.00",
        "expected_goals_conceded": "0.80",
        "defensive_contribution": 0,
        "clearances_blocks_interceptions": 2,
        "recoveries": 5,
        "tackles": 0,
        "value": 55,
        "transfers_balance": 1000,
        "selected": 500000,
        "transfers_in": 2000,
        "transfers_out": 1000,
    }


@respx.mock
def test_sync_current_season_end_to_end(tmp_path, load_fixture):
    """Live-sourced player_gw_history rows must be schema-identical to
    vaastav-loaded ones (same table, same columns) and re-running must be a
    no-op — the two acceptance criteria from issue #2."""
    bootstrap_json = load_fixture("bootstrap_static.json")
    fixtures_json = load_fixture("fixtures.json")
    respx.get(f"{BASE_URL}/bootstrap-static/").mock(
        return_value=httpx.Response(200, json=bootstrap_json)
    )
    respx.get(f"{BASE_URL}/fixtures/").mock(return_value=httpx.Response(200, json=fixtures_json))

    element_ids = [e["id"] for e in bootstrap_json["elements"]]
    for element_id in element_ids:
        payload = _one_history_row() if element_id == 1 else None
        body = {"fixtures": [], "history": [payload] if payload else [], "history_past": []}
        respx.get(f"{BASE_URL}/element-summary/{element_id}/").mock(
            return_value=httpx.Response(200, json=body)
        )

    con = db.connect(":memory:")
    db.init_schema(con)
    client = FplApiClient(cache_dir=tmp_path / "raw", min_interval=0.0)

    bootstrap = client.bootstrap_static()
    fixtures = client.fixtures()
    summary = live_gw.sync_current_season(con, client, bootstrap, fixtures, season="2099-00")

    assert summary == {
        "season": "2099-00",
        "teams": 20,
        "fixtures": 20,
        "players": len(element_ids),
        "gw_rows": 1,
    }

    row = con.execute(
        "SELECT gw, fixture_id, code, team_id, opponent_team_id, was_home, position, "
        "value, source FROM player_gw_history WHERE season = '2099-00'"
    ).fetchone()
    assert row == (1, 1, 154561, 12, 4, True, "GKP", 55, "fpl_api")

    player_row = con.execute(
        "SELECT web_name FROM players WHERE code = 154561"
    ).fetchone()
    assert player_row == ("Raya",)

    # write_features must run cleanly over live-sourced rows, not just vaastav's
    write_features(con)

    # re-running is a no-op: same upstream data (served from the client's file
    # cache) -> identical row count after DELETE+INSERT
    summary_again = live_gw.sync_current_season(con, client, bootstrap, fixtures, season="2099-00")
    assert summary_again["gw_rows"] == summary["gw_rows"]

    client.close()
    con.close()


@respx.mock
def test_live_and_vaastav_rows_coexist_in_same_table(tmp_path, load_fixture):
    """The literal acceptance check: load a vaastav season and a live season
    into the same player_gw_history table and rebuild features over both."""
    vaastav_dir = FIXTURES_DIR_VAASTAV
    bootstrap_json = load_fixture("bootstrap_static.json")
    fixtures_json = load_fixture("fixtures.json")
    respx.get(f"{BASE_URL}/bootstrap-static/").mock(
        return_value=httpx.Response(200, json=bootstrap_json)
    )
    respx.get(f"{BASE_URL}/fixtures/").mock(return_value=httpx.Response(200, json=fixtures_json))
    for element_id in [e["id"] for e in bootstrap_json["elements"]]:
        payload = _one_history_row() if element_id == 1 else None
        body = {"fixtures": [], "history": [payload] if payload else [], "history_past": []}
        respx.get(f"{BASE_URL}/element-summary/{element_id}/").mock(
            return_value=httpx.Response(200, json=body)
        )

    for name in ["teams.csv", "players_raw.csv", "fixtures.csv", "gws/merged_gw.csv"]:
        respx.get(f"{vaastav.RAW_BASE}/2021-22/{name}").mock(
            return_value=httpx.Response(200, content=(vaastav_dir / name).read_bytes())
        )

    con = db.connect(":memory:")
    db.init_schema(con)
    vaastav.load_all_seasons(con, cache_dir=tmp_path / "vaastav_raw", seasons=["2021-22"])

    client = FplApiClient(cache_dir=tmp_path / "raw", min_interval=0.0)
    bootstrap = client.bootstrap_static()
    fixtures = client.fixtures()
    live_gw.sync_current_season(con, client, bootstrap, fixtures, season="2099-00")
    client.close()

    n_seasons = con.execute(
        "SELECT COUNT(DISTINCT season) FROM player_gw_history"
    ).fetchone()[0]
    assert n_seasons == 2

    write_features(con)  # must not raise
    con.close()
