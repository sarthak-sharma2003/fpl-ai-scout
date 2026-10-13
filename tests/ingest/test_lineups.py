from datetime import UTC, datetime

import numpy as np
import pandas as pd

from fplscout import db
from fplscout.ingest import lineups

ROSTER = pd.DataFrame({
    "code": [1, 2, 3, 4],
    "first_name": ["Gabriel", "Gabriel", "Martin", "Hugo"],
    "second_name": ["dos Santos Magalhães", "Martinelli Silva", "Ødegaard", "Bueno"],
    "web_name": ["Gabriel", "Martinelli", "Ødegaard", "H.Bueno"],
})


def test_match_handles_accents_aliases_and_namesakes():
    got = lineups.match_team(
        {"Martin Odegaard": 0.8, "Magalhaes": 0.8, "Gabriel Martinelli": 0.2,
         "Santi Bueno": 0.8}, ROSTER)
    assert got == {3: 0.8, 1: 0.8, 2: 0.2}    # Santi Bueno is not Hugo Bueno


def _p(p0, p1, p60):
    return np.array([[p0, p1, p60]])


def test_overlay_moves_p60_the_measured_way():
    base = _p(0.1, 0.1, 0.8)
    xi = lineups.apply_overlay(base, [1], [10], {1: 0.8}, set())
    out = lineups.apply_overlay(base, [2], [10], {1: 0.8}, {10})   # complete XI, 2 unlisted
    assert xi[0, 2] > 0.8 > out[0, 2]
    assert abs(out[0, 2] - 0.51) < 0.02                            # doc table: 0.80 -> 0.51
    assert np.allclose(xi.sum(axis=1), 1) and np.allclose(out.sum(axis=1), 1)


def test_overlay_leaves_uncovered_and_incomplete_teams_alone():
    base = _p(0.2, 0.2, 0.6)
    assert np.allclose(lineups.apply_overlay(base, [5], [99], {1: 0.8}, {10}), base)
    # covered but XI not fully matched: an unlisted player is not "benched"
    assert np.allclose(lineups.apply_overlay(base, [2], [10], {1: 0.8}, set()), base)


def test_tagged_out_in_xi_counts_as_out():
    base = _p(0.1, 0.1, 0.8)
    out = lineups.apply_overlay(base, [1], [10], {1: 0.4}, set())
    zero = lineups.apply_overlay(base, [1], [10], {1: 0.0}, set())
    assert np.allclose(out, zero)


def _db():
    con = db.connect(":memory:")
    con.execute("INSERT INTO teams (season, team_id, code, name, short_name) VALUES "
                "('2026-27', 10, 3, 'Arsenal', 'ARS'), ('2026-27', 11, 7, 'Everton', 'EVE')")
    con.execute("INSERT INTO fixtures (season, fixture_id, event, kickoff_time, team_h, team_a) "
                "VALUES ('2026-27', 1, 6, '2026-10-11 15:00:00', 10, 11)")
    for r in ROSTER.itertuples():
        con.execute("INSERT INTO players (code, first_name, second_name, web_name) "
                    "VALUES (?, ?, ?, ?)", [r.code, r.first_name, r.second_name, r.web_name])
        con.execute("INSERT INTO player_season (season, element_id, code, team_id) "
                    "VALUES ('2026-27', ?, ?, 10)", [r.code, r.code])
    return con


DEADLINE = datetime(2026, 10, 17, 10, tzinfo=UTC)
NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


def test_sync_archives_a_fresh_file():
    con = _db()
    s = lineups.sync_rotowire(con, "2026-27", 7, DEADLINE, now=NOW,
                              data={"Arsenal": {"Martin Odegaard": 0.8}},
                              committed_at=datetime(2026, 10, 16, 8, tzinfo=UTC))
    assert s["teams"] == 1 and s["matched"] == 1
    levels, complete = lineups.lineup_inputs(con, "2026-27", 7)
    assert levels == {3: 0.8} and complete == set()


def test_sync_skips_a_file_older_than_the_teams_last_match():
    con = _db()
    # committed an hour after Sunday's 15:00 kickoff: still describes that match
    s = lineups.sync_rotowire(con, "2026-27", 7, datetime(2026, 10, 12, 12, tzinfo=UTC),
                              now=datetime(2026, 10, 12, 6, tzinfo=UTC),
                              data={"Arsenal": {"Martin Odegaard": 0.8}},
                              committed_at=datetime(2026, 10, 11, 16, tzinfo=UTC))
    assert s["teams"] == 0 and "predates" in s["skipped"][0]


def test_sync_refuses_confirmed_values_stale_files_and_passed_deadlines():
    con = _db()
    s = lineups.sync_rotowire(con, "2026-27", 7, DEADLINE, now=NOW,
                              data={"Arsenal": {"Martin Odegaard": 1.0}},
                              committed_at=datetime(2026, 10, 16, 8, tzinfo=UTC))
    assert s["teams"] == 0
    old = lineups.sync_rotowire(con, "2026-27", 7, DEADLINE, now=NOW, data={},
                                committed_at=datetime(2026, 10, 14, 8, tzinfo=UTC))
    assert "older than 48h" in old["note"]
    late = lineups.sync_rotowire(con, "2026-27", 7, DEADLINE,
                                 now=datetime(2026, 10, 17, 11, tzinfo=UTC), data={})
    assert late["note"] == "deadline passed"
