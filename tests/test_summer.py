"""Summer-form ingest: the parsing and the boost curve, which are the parts that
can be silently wrong. Network fetching is not tested here — same reasoning as
test_odds.py: `fplscout refresh` exercises it for real, and mocking httpx would
only assert that the mock works.
"""

from __future__ import annotations

import duckdb
import pytest

from fplscout import pipeline
from fplscout.ingest.summer import (
    _goal_count,
    name_to_code,
    normalise_name,
    parse_preseason,
    parse_world_cup,
)

# Two clubs' write-ups of the SAME friendly. Note the two spellings of Newcastle
# and the two spellings of Barnes's name — this is the real shape of the pages,
# not a contrived worst case.
EVERTON_PAGE = """==Pre-season and friendlies==
{{Football box collapsible
| date       = 12 August 2026
| team1      = [[Newcastle United F.C.|Newcastle United]]
| score      = 2–4
| team2      = Everton
| goals1     =
*[[Harvey Barnes|Barnes]] {{goal|20}}
*[[Dan Burn|Burn]] {{goal|55|o.g.}}
| goals2     =
*[[Iliman Ndiaye|Ndiaye]] {{goal|31||64}}
*[[Tyrique George|George]] {{goal|}}
| stadium    = [[St James' Park]]
}}

==Competitions==
{{Football box collapsible
| date       = 22 August 2026
| team1      = Everton
| team2      = Arsenal
| goals1     =
*[[Beto (footballer, born 1998)|Beto]] {{goal|12}}
}}
"""

NEWCASTLE_PAGE = """==Pre-season and friendlies==
{{football box collapsible
| date       = 12 August 2026
| team1      = Newcastle
| score      = 2–4
| team2      = [[Everton F.C.|Everton]]
| goals1     =
*[[Harvey Barnes]] {{goal|20}}
*[[Dan Burn|Burn]] {{goal|55|o.g.}}
| goals2     =
*[[Iliman Ndiaye|Ndiaye]] {{goal|31||64}}
*Proctor {{goal|}}
| penaltyscore = 1–3
| penalties1 =
*[[Harvey Barnes|Barnes]] {{pengoal}}
}}
"""


def test_goal_count_handles_the_template_variants():
    """One {{goal}} can carry several minutes — `{{goal|31||64}}` is a brace, and
    reading it as one goal halves every multi-goal performance in the dataset."""
    assert _goal_count("*[[X]] {{goal|20}}") == 1
    assert _goal_count("*[[X]] {{goal|31||64}}") == 2
    assert _goal_count("*[[X]] {{goal|12}} {{goal|45+2}}") == 2
    # minute unrecorded, but a goal was still scored
    assert _goal_count("*[[X]] {{goal|}}") == 1
    assert _goal_count("*[[X]] {{goal|pen.}}") == 1
    assert _goal_count("*[[X]] {{pengoal}}") == 0  # shootout, not a goal
    assert _goal_count("*[[X]]") == 0


def test_parse_preseason_reads_names_minutes_and_stops_at_the_next_section():
    parsed = parse_preseason(EVERTON_PAGE)
    by_name = {name: goals for name, goals in parsed.values()}

    # wikilink TARGET, not the display surname — the full name is what matches FPL
    assert by_name["Iliman Ndiaye"] == 2
    assert by_name["Harvey Barnes"] == 1
    assert by_name["Tyrique George"] == 1
    # own goal belongs to the other team; crediting it would boost the defender
    assert "Dan Burn" not in by_name
    # the Competitions box below the next heading uses the identical template —
    # sweeping the whole article folds real league goals into a pre-season score
    assert "Beto" not in by_name


def test_parse_preseason_dedupes_the_same_fixture_across_two_club_pages():
    """Regression: a PL-v-PL friendly is written up on both clubs' articles, and
    they spell the teams differently ("Newcastle" vs "[[Newcastle United F.C.]]"),
    so a fixture key built from team names never collapses them and every goal in
    the match counts twice. Keying on (date, scorer) dedupes by construction."""
    merged = {}
    merged.update(parse_preseason(EVERTON_PAGE))
    merged.update(parse_preseason(NEWCASTLE_PAGE))

    totals: dict[str, int] = {}
    for name, goals in merged.values():
        totals[name] = totals.get(name, 0) + goals

    assert totals["Harvey Barnes"] == 1  # not 2
    assert totals["Iliman Ndiaye"] == 2  # not 4
    # differing spellings of the same scorer-day still collapse to one entry
    assert ("12 august 2026", "harvey barnes") in merged
    # and a scorer only one page recorded survives
    assert totals["Proctor"] == 1


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
