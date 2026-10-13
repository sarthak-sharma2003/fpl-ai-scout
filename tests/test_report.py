from __future__ import annotations

import json

import pytest

from fplscout import db
from fplscout.report.weekly import render_weekly


def test_render_weekly_names_captain_and_sections():
    con = db.connect(":memory:")
    squad = list(range(1, 16))
    con.executemany(
        "INSERT INTO players (code, web_name) VALUES (?, ?)",
        [(c, f"Player{c}") for c in squad],
    )
    con.execute("UPDATE players SET penalties_order = 1 WHERE code = 8")
    con.execute("UPDATE players SET status = 'd', news = 'Knock - 75% chance' WHERE code = 13")
    con.executemany(
        "INSERT INTO features (season, code, fixture_id, gw, position) VALUES "
        "('2026-27', ?, ?, 1, ?)",
        [(c, 100 + c, "GKP" if c <= 2 else "DEF" if c <= 7 else "MID" if c <= 12 else "FWD")
         for c in squad],
    )
    con.execute(
        "INSERT INTO recommendations (season, gw, generated_at, squad, starting_xi, "
        "captain_code, vice_captain_code, transfers, hits, chip, confidence) VALUES "
        "('2026-27', 1, now(), ?, ?, 8, 9, '[]', 0, 'wildcard', NULL)",
        [json.dumps(squad), json.dumps([1, *range(3, 8), *range(8, 12), 13])],
    )

    sheet = render_weekly(con, "2026-27", 1)
    assert "**Captain:** Player8 [MID] ⚽PK" in sheet
    assert "**Vice:** Player9 [MID]" in sheet
    assert "Player13 [FWD] ⚠D (Knock - 75% chance)" in sheet
    assert "## Starting XI" in sheet and "## Bench" in sheet
    assert "Player2 [GKP]" in sheet  # backup GK on the bench
    assert "chip: **wildcard**" in sheet

    with pytest.raises(ValueError, match="no recommendation"):
        render_weekly(con, "2026-27", 2)
    con.close()


def test_weekly_sheet_lists_every_chip_verdict_hold_included():
    from fplscout.report.weekly import render_chips

    chips = {"season": "2026-27", "reference_gw": 7, "chips": [
        {"chip": "3xc", "start_gw": 1, "stop_gw": 19, "advice": "play", "bar": 8.0,
         "this_week_ev": 9.2, "this_week": {"name": "Haaland"}, "planned_gw": 7,
         "planned_ev": 9.2},
        {"chip": "wildcard", "start_gw": 2, "stop_gw": 19, "advice": "hold", "bar": 15.0,
         "this_week_ev": 14.1, "planned_gw": 7, "planned_ev": 14.1},
        {"chip": "bboost", "start_gw": 1, "stop_gw": 19, "advice": "planned", "bar": 15.0,
         "this_week_ev": 9.0, "planned_gw": 11, "planned_ev": 16.3},
        {"chip": "freehit", "start_gw": 2, "stop_gw": 19, "advice": "used", "used_gw": 5},
        {"chip": "wildcard", "start_gw": 20, "stop_gw": 38, "advice": "not_yet"},
    ]}
    text = "\n".join(render_chips(chips, 7))
    assert "## Chips (checked this run)" in text
    assert "**Triple Captain: PLAY IT THIS WEEK**" in text and "Haaland +9.2" in text
    assert "Wildcard: hold" in text and "misses the bar" in text
    assert "Bench Boost: hold for now, planned GW11 (+16.3)" in text
    assert "Free Hit: used (GW5)" in text
    assert "Second-half chips (Wildcard): not open yet" in text
