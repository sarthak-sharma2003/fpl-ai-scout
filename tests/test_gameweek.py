from datetime import datetime

import duckdb

from fplscout import db
from fplscout.gameweek import decision_gameweek


def _con(rows):
    con = duckdb.connect()
    db.init_schema(con)
    for event, deadline, finished in rows:
        con.execute(
            "INSERT INTO gameweeks (season, event, deadline_time, finished) VALUES (?, ?, ?, ?)",
            ["2026-27", event, deadline, finished],
        )
    return con


ROWS = [
    (5, datetime(2026, 9, 18, 17, 30), True),
    (6, datetime(2026, 10, 10, 10, 0), False),   # locked, not yet marked finished
    (7, datetime(2026, 10, 17, 10, 0), False),
    (8, datetime(2026, 10, 23, 17, 30), False),
]


def test_locked_but_unfinished_gameweek_is_not_the_decision():
    con = _con(ROWS)
    assert decision_gameweek(con, now=datetime(2026, 10, 13, 6, 0)) == ("2026-27", 7)


def test_before_deadline_decides_that_gameweek():
    con = _con(ROWS)
    assert decision_gameweek(con, now=datetime(2026, 10, 9, 12, 0)) == ("2026-27", 6)


def test_all_deadlines_past_falls_back_to_first_unfinished():
    con = _con(ROWS)
    assert decision_gameweek(con, now=datetime(2027, 6, 1)) == ("2026-27", 6)


def test_finished_season_has_no_decision():
    con = _con([(38, datetime(2026, 5, 24), True)])
    assert decision_gameweek(con, now=datetime(2026, 6, 1)) is None
