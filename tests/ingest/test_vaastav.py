from __future__ import annotations

from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from fplscout import db
from fplscout.ingest import vaastav

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "vaastav"


def _mock_season(season: str) -> None:
    base = FIXTURES_DIR / season
    respx.get(f"{vaastav.RAW_BASE}/{season}/teams.csv").mock(
        return_value=httpx.Response(200, content=(base / "teams.csv").read_bytes())
    )
    respx.get(f"{vaastav.RAW_BASE}/{season}/players_raw.csv").mock(
        return_value=httpx.Response(200, content=(base / "players_raw.csv").read_bytes())
    )
    respx.get(f"{vaastav.RAW_BASE}/{season}/fixtures.csv").mock(
        return_value=httpx.Response(200, content=(base / "fixtures.csv").read_bytes())
    )
    respx.get(f"{vaastav.RAW_BASE}/{season}/gws/merged_gw.csv").mock(
        return_value=httpx.Response(200, content=(base / "gws" / "merged_gw.csv").read_bytes())
    )


@pytest.fixture
def con():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@respx.mock
def test_load_two_seasons_end_to_end(con, tmp_path):
    _mock_season("2021-22")
    _mock_season("2025-26")

    summaries = vaastav.load_all_seasons(
        con, cache_dir=tmp_path / "raw", seasons=["2021-22", "2025-26"]
    )
    assert {s["season"] for s in summaries} == {"2021-22", "2025-26"}
    assert all(s["gw_rows"] > 0 for s in summaries)

    total = con.execute("SELECT COUNT(*) FROM player_gw_history").fetchone()[0]
    assert total == sum(s["gw_rows"] for s in summaries)


@respx.mock
def test_salah_code_is_stable_across_seasons(con, tmp_path):
    """Plan §Phase1 DoD: ID-mapping spot check — Salah traced across seasons."""
    _mock_season("2021-22")
    _mock_season("2025-26")
    vaastav.load_all_seasons(con, cache_dir=tmp_path / "raw", seasons=["2021-22", "2025-26"])

    rows = con.execute(
        "SELECT DISTINCT season, code FROM player_gw_history "
        "WHERE code = 118748 ORDER BY season"
    ).fetchall()
    seasons_seen = {r[0] for r in rows}
    assert {"2021-22", "2025-26"} <= seasons_seen

    player = con.execute(
        "SELECT first_name, second_name FROM players WHERE code = 118748"
    ).fetchone()
    assert player == ("Mohamed", "Salah")


@respx.mock
def test_haaland_absent_pre_transfer_present_after(con, tmp_path):
    """Haaland (code 223094) joined the PL for 2022-23 — must not appear in 2021-22
    data, and must appear once loaded for 2025-26."""
    _mock_season("2021-22")
    _mock_season("2025-26")
    vaastav.load_all_seasons(con, cache_dir=tmp_path / "raw", seasons=["2021-22", "2025-26"])

    seasons_with_haaland = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT season FROM player_gw_history WHERE code = 223094"
        ).fetchall()
    }
    assert "2021-22" not in seasons_with_haaland
    assert "2025-26" in seasons_with_haaland


@respx.mock
def test_defcon_null_pre_2526_populated_after(con, tmp_path):
    """Plan §6.3: DEFCON is missing-not-zero before 2025-26, populated from 2025-26."""
    _mock_season("2021-22")
    _mock_season("2025-26")
    vaastav.load_all_seasons(con, cache_dir=tmp_path / "raw", seasons=["2021-22", "2025-26"])

    non_null_2122 = con.execute(
        "SELECT COUNT(defensive_contribution) FROM player_gw_history WHERE season = '2021-22'"
    ).fetchone()[0]
    total_2122 = con.execute(
        "SELECT COUNT(*) FROM player_gw_history WHERE season = '2021-22'"
    ).fetchone()[0]
    assert non_null_2122 == 0
    assert total_2122 > 0

    non_null_2526 = con.execute(
        "SELECT COUNT(defensive_contribution) FROM player_gw_history WHERE season = '2025-26'"
    ).fetchone()[0]
    total_2526 = con.execute(
        "SELECT COUNT(*) FROM player_gw_history WHERE season = '2025-26'"
    ).fetchone()[0]
    assert non_null_2526 == total_2526


@respx.mock
def test_promoted_team_players_load_without_error(con, tmp_path):
    """Promoted teams (no prior-season PL history) must not crash the loader."""
    _mock_season("2025-26")
    vaastav.load_all_seasons(con, cache_dir=tmp_path / "raw", seasons=["2025-26"])
    teams = {
        r[0] for r in con.execute("SELECT name FROM teams WHERE season = '2025-26'").fetchall()
    }
    assert "Liverpool" in teams  # sanity: real team names loaded


def test_resolve_team_ids_raises_on_unknown_name():
    teams = pd.DataFrame(
        {"season": ["2099-00"], "team_id": [1], "name": ["Real Madrid"], "code": [1]}
    )
    gw = pd.DataFrame({"team_name": ["Not A Real Team"]})
    with pytest.raises(vaastav.TeamNameMismatchError):
        vaastav._resolve_team_ids(gw, teams, "2099-00")


def test_drop_exact_duplicate_rows_removes_identical_pair():
    gw = pd.DataFrame(
        {
            "gw": [1, 1, 2],
            "fixture_id": [1, 1, 5],
            "element_id": [10, 10, 20],
            "minutes": [0, 0, 90],
        }
    )
    result = vaastav._drop_exact_duplicate_rows(gw, "2099-00")
    assert len(result) == 2


def test_drop_exact_duplicate_rows_raises_on_conflicting_pair():
    gw = pd.DataFrame(
        {
            "gw": [1, 1],
            "fixture_id": [1, 1],
            "element_id": [10, 10],
            "minutes": [0, 90],  # disagree — must not silently pick one
        }
    )
    with pytest.raises(vaastav.UnexpectedDuplicateRowsError):
        vaastav._drop_exact_duplicate_rows(gw, "2099-00")


def test_resolve_codes_exact_join():
    players = pd.DataFrame(
        {"element_id": [1, 2], "code": [1001, 1002], "web_name": ["Foo", "Bar"]}
    )
    gw = pd.DataFrame({"element_id": [1, 2], "player_name": ["Foo", "Bar"]})
    result = vaastav._resolve_codes(gw, players, "2099-00")
    assert result["code"].tolist() == [1001, 1002]


def test_resolve_codes_fuzzy_fallback_for_unmatched_id():
    players = pd.DataFrame({"element_id": [1], "code": [1001], "web_name": ["Salah"]})
    # element_id 99 has no direct match — must fall back to fuzzy name match
    gw = pd.DataFrame({"element_id": [99], "player_name": ["Salah"]})
    result = vaastav._resolve_codes(gw, players, "2099-00")
    assert result["code"].tolist() == [1001]


def test_resolve_codes_raises_when_unresolvable():
    players = pd.DataFrame({"element_id": [1], "code": [1001], "web_name": ["Salah"]})
    gw = pd.DataFrame({"element_id": [99], "player_name": ["Totally Unrelated Name"]})
    with pytest.raises(ValueError, match="no resolvable code"):
        vaastav._resolve_codes(gw, players, "2099-00")


def test_schema_creates_all_tables():
    con = db.connect(":memory:")
    db.init_schema(con)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert tables == set(db.TABLES)
    con.close()
