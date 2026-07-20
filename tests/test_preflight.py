from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from fplscout import db
from fplscout.preflight import has_failures, run_preflight

SEASON, GW = "2026-27", 1
NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)

# legal 15: 2 GKP, 5 DEF, 5 MID, 3 FWD across 6 clubs (max 3 per club)
POSITIONS = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
TEAMS = [1, 2, 1, 1, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 6]
# XI = 1 GKP, 4 DEF, 4 MID, 2 FWD
XI = [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14]


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    db.init_schema(con)
    for team_id in set(TEAMS):
        con.execute(
            "INSERT INTO teams (season, team_id, code, name, short_name) VALUES (?, ?, ?, ?, ?)",
            [SEASON, team_id, 100 + team_id, f"Team {team_id}", f"T{team_id}"],
        )
    for code, (pos, team_id) in enumerate(zip(POSITIONS, TEAMS, strict=True), start=1):
        con.execute(
            "INSERT INTO players (code, web_name, status) VALUES (?, ?, 'a')",
            [code, f"P{code}"],
        )
        con.execute(
            "INSERT INTO features (season, gw, fixture_id, code, team_id, position, value) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [SEASON, GW, code, code, team_id, pos, 60],
        )
        con.execute(
            "INSERT INTO projections (season, gw, code, model_version, ev_points, "
            "q10_points, q90_points, generated_at) VALUES (?, ?, ?, 'v1', ?, ?, ?, ?)",
            [SEASON, GW, code, 3.0 + code * 0.3, 1.0, 8.0, NOW - timedelta(hours=2)],
        )
    con.execute(
        "INSERT INTO gameweeks (season, event, deadline_time, finished) VALUES (?, ?, ?, ?)",
        [SEASON, GW, NOW + timedelta(days=2), False],
    )
    _insert_rec(con)
    return con


def _insert_rec(con, squad=None, xi=None, captain=14, vice=13, chip="wildcard"):
    squad = squad if squad is not None else list(range(1, 16))
    xi = xi if xi is not None else XI
    con.execute(
        "INSERT INTO recommendations (season, gw, generated_at, squad, starting_xi, "
        "captain_code, vice_captain_code, transfers, hits, chip) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 0, ?)",
        [SEASON, GW, datetime.now(UTC), json.dumps(squad), json.dumps(xi), captain, vice, chip],
    )


def test_legal_fresh_squad_passes(con):
    findings = run_preflight(con, SEASON, GW, now=NOW)
    # the 15-player seed legitimately trips the small-universe WARN; nothing else
    assert [f for f in findings if f.check != "universe"] == []
    assert not has_failures(findings)


def test_no_recommendation_fails(con):
    con.execute("DELETE FROM recommendations")
    findings = run_preflight(con, SEASON, GW, now=NOW)
    assert has_failures(findings)
    assert findings[0].check == "recommendation"


def test_four_from_one_club_fails(con):
    con.execute("UPDATE features SET team_id = 1 WHERE code = 15")
    findings = run_preflight(con, SEASON, GW, now=NOW)
    assert any(
        f.level == "FAIL" and f.check == "legality" and "max 3" in f.detail for f in findings
    )


def test_injured_starter_fails_doubtful_warns(con):
    con.execute("UPDATE players SET status = 'i', news = 'Knee injury' WHERE code = 14")
    con.execute(
        "UPDATE players SET status = 'd', chance_of_playing_next_round = 75 WHERE code = 13"
    )
    findings = run_preflight(con, SEASON, GW, now=NOW)
    availability = [f for f in findings if f.check == "availability"]
    assert {f.level for f in availability} == {"FAIL", "WARN"}
    assert any("Knee injury" in f.detail for f in availability)


def test_captain_outside_xi_fails(con):
    con.execute("DELETE FROM recommendations")
    _insert_rec(con, captain=15)  # code 15 is on the bench
    findings = run_preflight(con, SEASON, GW, now=NOW)
    assert any(f.level == "FAIL" and "captain" in f.detail for f in findings)


def test_over_budget_wildcard_fails(con):
    con.execute("UPDATE features SET value = 130")  # 15 * 130 = 1950 > 1000
    findings = run_preflight(con, SEASON, GW, now=NOW)
    assert any(f.level == "FAIL" and f.check == "budget" for f in findings)


def test_stale_projections_warn(con):
    con.execute("UPDATE projections SET generated_at = ?", [NOW - timedelta(days=4)])
    findings = run_preflight(con, SEASON, GW, now=NOW)
    assert any(f.level == "WARN" and f.check == "freshness" for f in findings)
    # staleness alone must not block publishing
    assert not has_failures(findings)


def test_missing_ev_on_starter_fails(con):
    con.execute("DELETE FROM projections WHERE code = 3")
    findings = run_preflight(con, SEASON, GW, now=NOW)
    assert has_failures(findings)


def test_past_deadline_on_live_season_fails(con):
    con.execute("UPDATE gameweeks SET deadline_time = ?", [NOW - timedelta(hours=3)])
    findings = run_preflight(con, SEASON, GW, now=NOW)
    assert any(f.level == "FAIL" and f.check == "deadline" for f in findings)
