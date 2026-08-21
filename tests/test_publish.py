from __future__ import annotations

import pandas as pd
import pytest

from fplscout import db
from fplscout.publish import (
    _confidence,
    _player_card,
    build_chips,
    build_league,
    build_rules,
)


def test_confidence_decays_smoothly_not_floored_at_zero():
    # Real FPL quantile spreads routinely exceed EV itself (ev=6, q90-q10=10 is
    # a normal starter) — the naive "1 - spread" formula floored at 0 for every
    # player in practice; this checks the replacement formula differentiates.
    tight = pd.DataFrame(
        {"code": [1], "ev_points": [8.0], "q10_points": [6.0], "q90_points": [10.0]}
    )
    wide = pd.DataFrame(
        {"code": [1], "ev_points": [8.0], "q10_points": [1.0], "q90_points": [16.0]}
    )
    c_tight = _confidence(tight, {1})
    c_wide = _confidence(wide, {1})
    assert c_tight > c_wide
    assert 0 < c_wide < 100
    assert 0 < c_tight < 100


def test_confidence_empty_xi_is_zero():
    ref = pd.DataFrame({"code": [1], "ev_points": [8.0], "q10_points": [6.0], "q90_points": [10.0]})
    assert _confidence(ref, set()) == 0.0


def test_player_card_rounds_price_to_tenths_of_a_million():
    row = pd.Series(
        {"code": 123, "web_name": "Salah", "team_short": "LIV", "position": "MID",
         "price": 140, "ev_points": 6.2}
    )
    card = _player_card(row)
    assert card == {
        "code": 123, "name": "Salah", "team": "LIV", "position": "MID",
        "price": 14.0, "ev": 6.2,
    }


def test_player_card_handles_missing_ev(tmp_path):
    row = pd.Series(
        {"code": 1, "web_name": "X", "team_short": "AAA", "position": "GKP",
         "price": 40, "ev_points": float("nan")}
    )
    card = _player_card(row)
    assert card["ev"] is None


def test_build_rules_reads_yaml_seed(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "rules:\n"
        "  - id: r1\n"
        "    title: Title\n"
        "    body: \"  Some body text.  \"\n"
        "    enabled: false\n"
    )
    rules = build_rules(rules_path)
    assert rules == [{"id": "r1", "title": "Title", "body": "Some body text.", "enabled": False}]


def test_build_rules_empty_file(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text("rules: []\n")
    assert build_rules(rules_path) == []


# --- build_league ----------------------------------------------------------

SEASON, PICKS_GW, PROJ_GW = "2026-27", 1, 2
US, R1, R2 = 100, 201, 202


@pytest.fixture
def league_con():
    con = db.connect(":memory:")
    db.init_schema(con)
    con.execute(
        "INSERT INTO teams (season, team_id, code, name, short_name) "
        "VALUES (?, 1, 901, 'Team A', 'AAA')",
        [SEASON],
    )
    for code in range(1, 13):
        con.execute(
            "INSERT INTO players (code, web_name) VALUES (?, ?)", [code, f"P{code}"]
        )
        con.execute(
            "INSERT INTO features (season, gw, fixture_id, code, team_id, position, value) "
            "VALUES (?, ?, ?, ?, 1, 'MID', 60)",
            [SEASON, PROJ_GW, 1000 + code, code],
        )
        con.execute(
            "INSERT INTO projections (season, gw, code, model_version, ev_points, "
            "generated_at) VALUES (?, ?, ?, 'v1', ?, now())",
            [SEASON, PROJ_GW, code, code / 2],
        )
    con.execute(
        "INSERT INTO league_standings (league_id, entry_id, league_name, entry_name, "
        "player_name, rank, last_rank, total, event_total, fetched_at) VALUES "
        "(9, ?, 'L', 'Us', 'Me', 2, 2, 60, 60, now()), "
        "(9, ?, 'L', 'Rival One', 'R1', 1, 1, 70, 70, now()), "
        "(9, ?, 'L', 'Rival Two', 'R2', 3, 3, 50, 50, now())",
        [US, R1, R2],
    )
    picks = {
        US: [(3, 1, False), (6, 1, False), (7, 2, True)],
        R1: [(1, 1, False), (2, 1, False), (3, 2, True), (4, 0, False)],
        R2: [(2, 2, True), (3, 1, False), (5, 1, False)],
    }
    for entry, entry_picks in picks.items():
        for i, (code, mult, is_cap) in enumerate(entry_picks):
            con.execute(
                "INSERT INTO rival_picks (season, gw, entry_id, element_id, code, "
                "pick_position, multiplier, is_captain, is_vice_captain) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, false)",
                [SEASON, PICKS_GW, entry, 500 + code, code, i + 1, mult, is_cap],
            )
        con.execute(
            "INSERT INTO rival_gw (season, gw, entry_id, points, total_points, bank, "
            "team_value, active_chip) VALUES (?, ?, ?, 60, 60, 15, 1002, ?)",
            [SEASON, PICKS_GW, entry, "wildcard" if entry == R1 else None],
        )
    yield con
    con.close()


def test_build_league_unconfigured_shell():
    con = db.connect(":memory:")
    db.init_schema(con)
    out = build_league(con, SEASON, PROJ_GW)
    con.close()
    assert out["configured"] is False
    assert "mini_league_id" in out["note"]


def test_build_league_standings_squads_and_ev(league_con):
    out = build_league(league_con, SEASON, PROJ_GW, our_entry_id=US)
    assert out["configured"] is True
    assert out["picks_gw"] == PICKS_GW and out["projection_gw"] == PROJ_GW

    by_name = {s["entry_name"]: s for s in out["standings"]}
    assert by_name["Us"]["is_us"] is True
    assert by_name["Rival One"]["chips_used"] == [{"chip": "wildcard", "gw": 1}]
    # R1 EV: 0.5 + 1.0 + captain 1.5*2 = 4.5; bench (mult 0) excluded
    assert by_name["Rival One"]["projected_next_ev"] == 4.5
    assert by_name["Rival One"]["captain"]["name"] == "P3"
    assert by_name["Rival One"]["bank"] == 1.5
    assert by_name["Rival One"]["team_value"] == 100.2


def test_build_league_ownership_and_differentials(league_con):
    out = build_league(league_con, SEASON, PROJ_GW, our_entry_id=US)
    own = {c["code"]: c for c in out["ownership"]}
    # codes 2 and 3 are owned by both rivals; 3 is also ours
    assert own[2]["n_owned"] == 2 and own[2]["we_own"] is False
    assert own[3]["n_owned"] == 2 and own[3]["we_own"] is True
    assert own[2]["n_captained"] == 1

    threats = [c["code"] for c in out["differentials"]["threats"]]
    assert threats == [2]  # both rivals own it, we don't; 5 is single-owned
    edges = [c["code"] for c in out["differentials"]["our_edges"]]
    # ours that <=1 rival owns, by EV desc: 7 (3.5), 6 (3.0); 3 excluded (2 rivals)
    assert edges == [7, 6]


# --- build_chips -----------------------------------------------------------


def test_build_chips_unconfigured_note():
    con = db.connect(":memory:")
    db.init_schema(con)
    out = build_chips(con, SEASON, 1)
    con.close()
    assert out["configured"] is False and out["chips"] == []


def test_build_chips_windows_usage_observables_and_radar(league_con):
    con = league_con
    con.executemany(
        "INSERT INTO chip_windows (season, chip_id, chip, number, start_event, "
        "stop_event, chip_type) VALUES (?, ?, ?, 1, ?, ?, 'team')",
        [
            (SEASON, 1, "wildcard", 2, 19),
            (SEASON, 2, "wildcard", 20, 38),
            (SEASON, 3, "bboost", 1, 19),
            (SEASON, 4, "3xc", 1, 19),
        ],
    )
    # our wildcard burned at GW1 (rival_gw seeded with active_chip for R1... use R1 as "us")
    con.execute(
        "INSERT INTO recommendations (season, gw, generated_at, squad, starting_xi, "
        "captain_code, vice_captain_code, transfers, hits, chip) VALUES "
        "(?, ?, now(), '[1,2,3,4]', '[1,2]', 3, 2, '[]', 0, NULL)",
        [SEASON, PROJ_GW],
    )
    # a double for team 1 in PROJ_GW+1: two fixtures same event
    con.executemany(
        "INSERT INTO fixtures (season, fixture_id, event, team_h, team_a, finished) "
        "VALUES (?, ?, ?, ?, ?, false)",
        [
            (SEASON, 1, PROJ_GW + 1, 1, 2),
            (SEASON, 2, PROJ_GW + 1, 2, 1),
        ],
    )
    con.execute(
        "INSERT INTO teams (season, team_id, code, name, short_name) "
        "VALUES (?, 2, 902, 'Team B', 'BBB')",
        [SEASON],
    )

    out = build_chips(con, SEASON, PROJ_GW, our_entry_id=R1)
    assert out["configured"] is True

    by_key = {(c["chip"], c["start_gw"]): c for c in out["chips"]}
    # R1's wildcard fired at GW1... GW1 predates the first WC window (starts 2) —
    # no window matches, so both wildcards stay available
    assert by_key[("wildcard", 2)]["available"] is True

    # bench = squad [1,2,3,4] minus XI [1,2] -> evs 1.5 + 2.0 = 3.5
    assert by_key[("bboost", 1)]["this_week"]["bench_ev"] == 3.5
    # captain code 3 -> ev 1.5
    tc = by_key[("3xc", 1)]["this_week"]
    assert tc["name"] == "P3" and tc["extra_ev"] == 1.5

    radar = {r["gw"]: r for r in out["dgw_bgw_radar"]}
    assert radar[PROJ_GW + 1]["dgw_teams"] == ["AAA", "BBB"]


def test_build_chips_marks_used_window(league_con):
    con = league_con
    con.execute(
        "INSERT INTO chip_windows (season, chip_id, chip, number, start_event, "
        "stop_event, chip_type) VALUES (?, 9, 'wildcard', 1, 1, 19, 'transfer')",
        [SEASON],
    )
    out = build_chips(con, SEASON, PROJ_GW, our_entry_id=R1)
    wc = out["chips"][0]
    assert wc["available"] is False and wc["used_gw"] == 1
