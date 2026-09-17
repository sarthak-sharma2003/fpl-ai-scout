"""European results ingest: name resolution and event normalisation, the parts
that can be silently wrong. Network fetching is not tested here — it's
exercised for real by `fplscout refresh`.
"""

from __future__ import annotations

import duckdb

from fplscout.ingest.european import _normalise, _resolve_team_ids


def _con_with_teams() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE TABLE teams (season TEXT, team_id INTEGER, name TEXT)")
    con.execute(
        "INSERT INTO teams VALUES "
        "('2026-27', 8, 'Crystal Palace'), ('2026-27', 16, 'Man Utd'), "
        "('2026-27', 19, 'Spurs')"
    )
    return con


def _event(
    event_id: str,
    home_name: str,
    away_name: str,
    home_score: str | None,
    away_score: str | None,
    completed: bool,
) -> dict:
    return {
        "id": event_id,
        "competitions": [{
            "date": "2026-09-17T19:00Z",
            "status": {"type": {"completed": completed}},
            "competitors": [
                {
                    "homeAway": "home",
                    "score": home_score,
                    "team": {"displayName": home_name},
                },
                {
                    "homeAway": "away",
                    "score": away_score,
                    "team": {"displayName": away_name},
                },
            ],
        }],
    }


def test_resolve_team_ids_handles_espn_full_names_via_override():
    """ESPN's "Manchester United"/"Tottenham Hotspur" share no prefix with
    FPL's "Man Utd"/"Spurs" — without the override these silently drop out of
    coverage all season with nothing failing."""
    con = _con_with_teams()
    mapping = _resolve_team_ids(con, "2026-27")
    assert mapping["Manchester United"] == 16
    assert mapping["Tottenham Hotspur"] == 19


def test_resolve_team_ids_prefix_matches_unlisted_clubs():
    """A club with no override entry (e.g. Crystal Palace, byte-identical
    already) still resolves via the plain by-name lookup."""
    con = _con_with_teams()
    mapping = _resolve_team_ids(con, "2026-27")
    assert mapping["Crystal Palace"] == 8


def test_normalise_skips_the_foreign_side_without_erroring():
    """Unlike odds.py (always two PL clubs), most European fixtures have one
    foreign opponent — that side must be silently dropped, not raised on."""
    con = _con_with_teams()
    events = [_event("1", "Real Sociedad", "Crystal Palace", "1", "0", True)]
    out = _normalise(events, "2026-27", "UEL", _resolve_team_ids(con, "2026-27"))

    assert len(out) == 1
    row = out.iloc[0]
    assert row["team_id"] == 8
    assert row["opponent"] == "Real Sociedad"
    assert not row["was_home"]
    assert row["goals_for"] == 0 and row["goals_against"] == 1


def test_normalise_leaves_scores_null_until_full_time():
    """A live or unplayed fixture must not masquerade as a 0-0 result."""
    con = _con_with_teams()
    events = [_event("2", "Crystal Palace", "Lech Poznan", "0", "0", False)]
    out = _normalise(events, "2026-27", "UEL", _resolve_team_ids(con, "2026-27"))

    row = out.iloc[0]
    assert not row["finished"]
    assert row["goals_for"] is None and row["goals_against"] is None


def test_normalise_emits_both_sides_for_an_all_pl_tie():
    con = _con_with_teams()
    events = [_event("3", "Manchester United", "Tottenham Hotspur", "2", "1", True)]
    out = _normalise(events, "2026-27", "UEL", _resolve_team_ids(con, "2026-27"))

    assert len(out) == 2
    by_team = out.set_index("team_id")
    assert by_team.loc[16, "goals_for"] == 2 and by_team.loc[16, "was_home"]
    assert by_team.loc[19, "goals_for"] == 1 and not by_team.loc[19, "was_home"]


def test_normalise_returns_empty_frame_for_no_events():
    con = _con_with_teams()
    out = _normalise([], "2026-27", "UEL", _resolve_team_ids(con, "2026-27"))
    assert len(out) == 0
