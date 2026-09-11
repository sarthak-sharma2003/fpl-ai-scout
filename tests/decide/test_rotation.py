"""Rotation pairs on a hand-built fixture run where the right partner is known
by inspection."""

from __future__ import annotations

import pandas as pd

from fplscout.decide.rotation import rotation_pairs

GWS = [4, 5, 6, 7, 8, 9]


def _universe():
    ev = pd.DataFrame(
        {
            1: [5, 1, 5, 1, 5, 1],  # owned DEF, club 1: good in GW4/6/8
            2: [1, 5, 1, 5, 1, 5],  # DEF, club 2: the mirror image, the rotation
            3: [1, 9, 1, 9, 1, 9],  # DEF, club 1: same club as 1, same fixtures
            4: [6, 6, 6, 6, 6, 6],  # DEF, club 3: better every week, an upgrade
            5: [1, 9, 1, 9, 1, 9],  # MID: wrong position
        },
        index=GWS,
    ).T
    universe = pd.DataFrame({
        "code": [1, 2, 3, 4, 5],
        "position": ["DEF", "DEF", "DEF", "DEF", "MID"],
        "team_id": [1, 2, 1, 3, 4],
    })
    return ev, universe


def test_picks_the_alternating_partner_net_of_a_hit():
    ev, universe = _universe()
    [pair] = rotation_pairs(ev, universe, owned={1}, decay=0.5, hit_cost=1.0)
    assert pair["partner"] == 2
    # partner starts GW5/7/9 (h = 1, 3, 5) for +4 each, decayed, less the hit
    assert pair["net_gain"] == round(4 * (0.5 + 0.5**3 + 0.5**5) - 1.0, 2)
    assert [w["start"] for w in pair["weeks"]] == ["owned", "partner"] * 3


def test_no_pair_when_the_hit_eats_the_gain():
    ev, universe = _universe()
    assert rotation_pairs(ev, universe, owned={1}, decay=0.5, hit_cost=10.0) == []


def test_owned_players_are_never_partners():
    ev, universe = _universe()
    # 2 is 1's natural partner, but already owned: the weekly XI picks between
    # them. 2 still gets its own partner, 3.
    pairs = rotation_pairs(ev, universe, owned={1, 2}, decay=0.5, hit_cost=1.0)
    assert [(p["owned"], p["partner"]) for p in pairs] == [(2, 3)]
