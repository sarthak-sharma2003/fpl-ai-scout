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
