"""Odds ingest: the maths and the orientation, which are the parts that can be
silently wrong. Network fetching is not tested here — it's exercised for real by
`fplscout refresh`, and mocking httpx would only assert that the mock works.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from fplscout.ingest.odds import (
    _normalise,
    _poisson_p_over_25,
    devig,
    devig_two_way,
    expected_total_goals,
    looks_like_csv,
)


def test_looks_like_csv_accepts_bom_and_rejects_html():
    """Regression: bytes.lstrip() does not strip a UTF-8 BOM, and some seasons'
    files carry one. The BOM-blind version silently dropped 3 of 5 seasons."""
    assert looks_like_csv(b"\xef\xbb\xbfDiv,Date,HomeTeam")
    assert looks_like_csv(b"Div,Date,HomeTeam")
    assert not looks_like_csv(b"<!DOCTYPE HTML><html>301 Moved</html>")


def test_devig_two_way_recovers_probability_from_odds():
    """Over/under columns are DECIMAL ODDS, not probabilities — feeding the raw
    2.09 straight into the Poisson inversion produced all-NULL goal expectations."""
    p_over = devig_two_way(2.09, 1.76)
    assert 0.0 < p_over < 1.0
    assert p_over == pytest.approx(1 / 2.09 / (1 / 2.09 + 1 / 1.76))
    # shorter odds on over => higher probability of over
    assert devig_two_way(1.50, 2.50) > devig_two_way(2.50, 1.50)
    assert np.isnan(devig_two_way(0.0, 1.8))


def test_devig_sums_to_one_and_removes_margin():
    p_home, p_draw, p_away = devig(2.0, 3.5, 4.0)
    assert p_home + p_draw + p_away == pytest.approx(1.0)
    # raw 1/odds overrounds to >1; each de-vigged prob must land below its raw
    assert p_home < 1 / 2.0
    assert p_home > p_draw > p_away


def test_devig_handles_garbage():
    assert np.isnan(devig(0.0, 3.0, 4.0)[0])
    assert np.isnan(devig(float("nan"), 3.0, 4.0)[0])


def test_expected_total_goals_inverts_poisson():
    # round trip: goals -> P(over 2.5) -> goals
    for goals in (1.5, 2.7, 4.0):
        assert expected_total_goals(_poisson_p_over_25(goals)) == pytest.approx(goals, abs=1e-6)


def test_expected_total_goals_is_monotone_and_guards_bad_input():
    assert expected_total_goals(0.35) < expected_total_goals(0.65)
    assert np.isnan(expected_total_goals(0.0))
    assert np.isnan(expected_total_goals(1.5))


def _con_with_teams() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE TABLE teams (season TEXT, team_id INTEGER, name TEXT)")
    con.execute(
        "INSERT INTO teams VALUES ('2022-23', 1, 'Man City'), ('2022-23', 2, 'Spurs')"
    )
    return con


def test_normalise_orients_supremacy_to_the_home_side():
    """AHh is the handicap on the HOME team, so a home favourite carries a
    NEGATIVE AHh and must come out with the LARGER expected goals. Getting this
    sign backwards would invert every fixture in the dataset while still
    producing entirely plausible-looking numbers."""
    from fplscout.ingest.odds import _resolve_team_ids

    con = _con_with_teams()
    raw = pd.DataFrame(
        [{
            "HomeTeam": "Man City", "AwayTeam": "Tottenham",
            "AvgH": 1.20, "AvgD": 7.0, "AvgA": 12.0,
            "Avg>2.5": 1.67, "Avg<2.5": 2.20, "AHh": -1.75,
        }]
    )
    out = _normalise(raw, "2022-23", _resolve_team_ids(con, "2022-23"))

    assert len(out) == 1
    row = out.iloc[0]
    assert row["home_team_id"] == 1 and row["away_team_id"] == 2
    assert row["p_home_win"] > row["p_away_win"]
    assert row["exp_goals_home"] > row["exp_goals_away"]
    # supremacy == -AHh, split evenly around the total
    assert row["exp_goals_home"] - row["exp_goals_away"] == pytest.approx(1.75)
    assert row["exp_goals_home"] + row["exp_goals_away"] == pytest.approx(
        row["exp_total_goals"]
    )


def test_normalise_raises_on_unmapped_team():
    """A club we can't map would otherwise become NULL odds for all its players
    for a whole season, with nothing failing loudly."""
    from fplscout.ingest.odds import _resolve_team_ids

    con = _con_with_teams()
    raw = pd.DataFrame(
        [{
            "HomeTeam": "Man City", "AwayTeam": "Real Madrid",
            "AvgH": 1.20, "AvgD": 7.0, "AvgA": 12.0, "Avg>2.5": 1.67, "Avg<2.5": 2.20, "AHh": -1.75,
        }]
    )
    with pytest.raises(ValueError, match="Real Madrid"):
        _normalise(raw, "2022-23", _resolve_team_ids(con, "2022-23"))
