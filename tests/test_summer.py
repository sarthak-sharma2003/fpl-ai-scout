"""Summer-form ingest: the aggregation and the boost curve, which are the parts
that can be silently wrong. Network fetching is not tested here — same reasoning
as test_odds.py: `fplscout refresh` exercises it for real, and mocking httpx
would only assert that the mock works.
"""

from __future__ import annotations

import duckdb
import pytest

from fplscout import pipeline
from fplscout.ingest.summer import (
    aggregate_preseason,
    name_to_code,
    normalise_name,
    parse_world_cup,
)

# Real nextXI row shape: minutes_played is NULL for an unused sub, assists are
# sparse, and opposition players ride along in the same table.
PRESEASON_ROWS = [
    {"display_name": "João Pedro", "player_name": "Joao Pedro Junqueira", "is_pl_team": True,
     "started": True, "minutes_played": 90, "goals": 2, "assists": 0},
    {"display_name": "João Pedro", "player_name": "Joao Pedro Junqueira", "is_pl_team": True,
     "started": True, "minutes_played": 62, "goals": 1, "assists": 1},
    {"display_name": "João Pedro", "player_name": "Joao Pedro Junqueira", "is_pl_team": True,
     "started": False, "minutes_played": None, "goals": 0, "assists": 0},
    {"display_name": "Some Opponent", "player_name": "Some Opponent", "is_pl_team": False,
     "started": True, "minutes_played": 90, "goals": 3, "assists": 2},
    {"display_name": "", "player_name": "", "is_pl_team": True,
     "started": True, "minutes_played": 90, "goals": 1, "assists": 0},
]


def test_aggregate_preseason_sums_per_player_and_drops_the_opposition():
    out = aggregate_preseason(PRESEASON_ROWS)

    pedro = out[normalise_name("João Pedro")]
    assert pedro["goals"] == 3.0
    assert pedro["assists"] == 1.0
    assert pedro["minutes"] == 152.0  # NULL minutes is an unused sub, worth 0
    assert pedro["starts"] == 2.0
    assert pedro["apps"] == 3.0  # ...but the unused sub IS an appearance

    # is_pl_team=False is an opponent. The Wikipedia scraper this replaced had to
    # exclude them by name-matching and got it wrong; here the flag is explicit.
    assert normalise_name("Some Opponent") not in out
    assert "" not in out  # a nameless row can never resolve to a code


def test_aggregate_preseason_keeps_the_source_spelling_for_matching():
    """The name is carried through so name_to_code sees what nextXI actually
    wrote — the accent-stripped key is for joining, not for display."""
    out = aggregate_preseason(PRESEASON_ROWS)
    assert out[normalise_name("João Pedro")]["name"] == "João Pedro"


def test_parse_world_cup_drops_own_goals_and_keeps_penalties():
    payload = {
        "matches": [
            {
                "goals1": [
                    {"name": "Erling Haaland", "minute": "9"},
                    {"name": "Erling Haaland", "minute": "67", "penalty": True},
                ],
                "goals2": [{"name": "Marc Guéhi", "minute": "30", "owngoal": True}],
            },
            {"goals1": [{"name": "Erling Haaland", "minute": "12"}], "goals2": []},
            {"score": {"ft": [0, 0]}},  # a match with no goal lists at all
        ]
    }
    goals = parse_world_cup(payload)
    assert goals["Erling Haaland"] == 3  # penalties count, FPL pays for them
    assert "Marc Guéhi" not in goals


def test_normalise_name_bridges_the_three_spellings():
    assert normalise_name("Ismaïla Sarr") == normalise_name("Ismaila Sarr")
    assert normalise_name("Aït-Nouri") == normalise_name("Ait Nouri")


def _con_with_players() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE players (code BIGINT, first_name TEXT, second_name TEXT, web_name TEXT)"
    )
    con.execute("CREATE TABLE player_season (season TEXT, code BIGINT)")
    con.execute(
        """INSERT INTO players VALUES
        (1, 'Ismaïla', 'Sarr', 'Sarr'),
        (2, 'Erling', 'Haaland', 'Haaland'),
        (3, 'Danny', 'Welbeck', 'Welbeck'),
        (4, 'Wesley', 'Welbeck', 'Welbeck'),
        (9, 'Departed', 'Player', 'Departed')"""
    )
    con.execute(
        "INSERT INTO player_season VALUES ('2026-27', 1), ('2026-27', 2), "
        "('2026-27', 3), ('2026-27', 4), ('2025-26', 9)"
    )
    return con


def test_name_to_code_matches_across_accents_and_refuses_to_guess():
    lookup = name_to_code(_con_with_players(), "2026-27")

    assert lookup[normalise_name("Ismaila Sarr")] == 1  # accents bridged
    assert lookup[normalise_name("Haaland")] == 2  # web-name fallback
    # two players share a surname: a coin flip would credit the wrong one, so the
    # key is dropped and they simply get no boost
    assert normalise_name("Welbeck") not in lookup
    # scoped to the season — a departed player at a foreign club must not match an
    # opposition scorer in a pre-season friendly
    assert normalise_name("Departed Player") not in lookup


def _con_with_summer_form(rows: list[tuple], finished_gws: int = 0):
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE summer_form (code BIGINT, player_name TEXT, "
        "wc_goals DOUBLE, preseason_goals DOUBLE)"
    )
    con.executemany("INSERT INTO summer_form VALUES (?, ?, ?, ?)", rows)
    con.execute("CREATE TABLE gameweeks (season TEXT, event INTEGER, finished BOOLEAN)")
    for gw in range(1, finished_gws + 1):
        con.execute("INSERT INTO gameweeks VALUES ('2026-27', ?, true)", [gw])
    return con


def test_summer_boost_is_small_bounded_and_only_for_the_exceptional():
    con = _con_with_summer_form(
        [
            (1, "Golden Boot", 7.0, 0.0),  # score 10.5 — way past the cap
            (2, "Good summer", 3.0, 1.0),  # score 5.5
            (3, "One goal", 0.0, 1.0),  # score 1.0 — below threshold
            (4, "Right at it", 2.0, 0.0),  # score 3.0 — exactly the threshold
        ]
    )
    boost = pipeline.summer_boost(con, "2026-27")

    assert boost[1] == pytest.approx(1.0 + pipeline.SUMMER_MAX_BOOST)
    assert boost[2] == pytest.approx(1.0 + 0.015 * 2.5)
    # below or at the threshold => omitted entirely, not mapped to 1.0
    assert 3 not in boost and 4 not in boost
    assert max(boost.values()) <= 1.0 + pipeline.SUMMER_MAX_BOOST


def test_summer_boost_fades_out_once_real_gameweeks_exist():
    """A July friendly must not still be nudging a player in April — by then the
    rolling features have measured the same thing directly, and better."""
    rows = [(1, "Golden Boot", 7.0, 0.0)]
    full = pipeline.summer_boost(_con_with_summer_form(rows, finished_gws=0), "2026-27")[1]
    half = pipeline.summer_boost(
        _con_with_summer_form(rows, finished_gws=pipeline.SUMMER_FADE_GWS // 2), "2026-27"
    )[1]

    assert full > half > 1.0
    assert half - 1.0 == pytest.approx((full - 1.0) / 2)
    assert pipeline.summer_boost(
        _con_with_summer_form(rows, finished_gws=pipeline.SUMMER_FADE_GWS), "2026-27"
    ) == {}
