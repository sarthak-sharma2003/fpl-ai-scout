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
