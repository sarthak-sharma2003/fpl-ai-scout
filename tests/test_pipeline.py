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


def test_unpickable_blocks_only_players_with_no_way_back():
    """Zero EV and "cannot play" look identical to a MILP maximising points, and
    at the price floor that makes an unavailable player ideal-looking bench
    fodder. A season-long loanee reached the published GW1 bench exactly that
    way: valued correctly at ~0, then picked because of it."""
    con = _con_with_news([
        (1, "u", "Has joined KVC Westerlo on loan for the rest of the season."),
        (2, "i", "Groin injury - Unknown return date"),
        (3, "s", "Suspended until 29 Aug"),
        (4, "i", "Ankle injury - Expected back 9 Jan"),
        (5, "a", None),
    ])
    for code in (1, 2, 3, 4):
        con.execute("UPDATE players SET chance_of_playing_next_round = 0 WHERE code = ?", [code])

    blocked = pipeline.unpickable(con, "2026-27", decision_gw=1)
    con.close()

    assert 1 in blocked, "left the league — can never autosub, not cheap fodder"
    assert 2 in blocked, "out now with no published return date"
    # out now, but the news says he is back next gameweek: a real wildcard buy
    assert 3 not in blocked
    # back in GW5, beyond an 8-gameweek horizon from GW1? no — GW5 is inside it
    assert 4 not in blocked
    assert 5 not in blocked, "a fit player must never be filtered out"


def test_unpickable_blocks_a_return_beyond_the_horizon():
    con = _con_with_news([(1, "i", "Expected back 9 Jan")])
    con.execute("UPDATE players SET chance_of_playing_next_round = 0 WHERE code = 1")
    # deciding GW1 with an 8-gameweek horizon: a GW5 return is inside it, but
    # deciding from a gameweek far enough back it would not be.
    assert 1 not in pipeline.unpickable(con, "2026-27", decision_gw=1)
    con.close()


def test_xi_minutes_floor_lapses_once_real_gameweeks_exist():
    """The floor is a season-opening crutch. Once roll5_started_share measures
    this season's minutes directly the model needs no help, and a permanent bar
    on low-minutes players would just be a worse model."""
    con = _con_with_news([(1, "a", None), (2, "a", None)])
    con.execute(
        "INSERT INTO projections (season, gw, code, model_version, p_60_plus) VALUES "
        "('2026-27', 1, 1, 'v1', 0.51), ('2026-27', 1, 2, 'v1', 0.88)"
    )
    assert pipeline.xi_minutes_floor(con, "2026-27", 1, "v1") == {1}

    for gw in range(1, pipeline.MINUTES_FLOOR_GWS + 1):
        con.execute(
            "UPDATE gameweeks SET finished = true WHERE season = '2026-27' AND event = ?",
            [gw],
        )
    assert pipeline.xi_minutes_floor(con, "2026-27", 1, "v1") == set()
    con.close()


def test_xi_minutes_floor_ignores_projections_without_p60():
    """An older projections row must degrade to previous behaviour, not bar
    every player in the pool."""
    con = _con_with_news([(1, "a", None)])
    con.execute(
        "INSERT INTO projections (season, gw, code, model_version, p_60_plus) "
        "VALUES ('2026-27', 1, 1, 'v1', NULL)"
    )
    assert pipeline.xi_minutes_floor(con, "2026-27", 1, "v1") == set()
    con.close()
