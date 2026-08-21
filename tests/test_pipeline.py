from __future__ import annotations

from fplscout import db, pipeline


def test_live_availability_factor_prefers_chance_then_status():
    con = db.connect(":memory:")
    db.init_schema(con)
    con.execute(
        "INSERT INTO players (code, status, chance_of_playing_next_round) VALUES "
        "(1, 'a', NULL), "  # available, no chance published -> 1.0
        "(2, 'i', NULL), "  # injured, no chance published -> 0.0
        "(3, 'd', 75), "  # doubtful but chance published -> 0.75
        "(4, 'a', 0), "  # chance published even though status is 'a' -> 0.0
        "(5, NULL, NULL)"  # never synced -> no information -> 1.0, NOT ruled out
    )
    factor = pipeline.live_availability_factor(con)
    con.close()
    assert factor == {1: 1.0, 2: 0.0, 3: 0.75, 4: 0.0, 5: 1.0}


def _con_with_news(rows: list[tuple]) -> object:
    """rows: (code, status, news). Gameweek deadlines span a new year, since
    `news` gives a day and month with no year ("6 Sep") and the resolver has to
    pick the right one from the real calendar."""
    con = db.connect(":memory:")
    db.init_schema(con)
    for event, deadline in [
        (1, "2026-08-21"), (2, "2026-08-29"), (3, "2026-09-05"),
        (4, "2026-10-03"), (5, "2027-01-09"),
    ]:
        con.execute(
            "INSERT INTO gameweeks (season, event, deadline_time, finished) "
            "VALUES (?, ?, ?, false)",
            ["2026-27", event, deadline],
        )
    for code, status, news in rows:
        con.execute(
            "INSERT INTO players (code, status, news) VALUES (?, ?, ?)", [code, status, news]
        )
    return con


def test_availability_return_gw_reads_dates_fpl_already_published():
    """The horizon used to guess return dates that FPL prints in plain text, and
    faded departed players back to fully fit by 4 gameweeks out — a player who
    had left the league carried 4.16 EV across gameweeks 5-8."""
    con = _con_with_news([
        (1, "s", "Suspended until 29 Aug"),
        (2, "i", "Ankle injury - Expected back 3 Oct"),
        (3, "i", "Knee injury - Unknown return date"),
        (4, "u", "has departed the club as a free agent."),
        (5, "a", None),
        (6, "i", "Groin injury - Expected back 31 Feb"),  # not a real date
    ])
    out = pipeline.availability_return_gw(con, "2026-27")
    con.close()

    assert out[1] == 2  # first deadline on or after 29 Aug
    assert out[2] == 4
    # no date published means we genuinely don't know — omitted so the caller
    # keeps the linear fade rather than reading absence as "available now"
    assert 3 not in out and 5 not in out and 6 not in out
    assert out[4] >= 900  # departed: out for every gameweek in any horizon


def test_availability_return_gw_picks_the_year_that_lands_in_the_season():
    """A season straddles new year, and the news text carries no year at all."""
    con = _con_with_news([(1, "i", "Expected back 9 Jan")])
    out = pipeline.availability_return_gw(con, "2026-27")
    con.close()
    assert out[1] == 5  # the 2027-01-09 gameweek, not a 2026 one
