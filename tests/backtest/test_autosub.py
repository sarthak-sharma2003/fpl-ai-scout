from __future__ import annotations

from fplscout.backtest.autosub import apply_autosubs


def _positions():
    return {
        1: "GKP", 2: "GKP",
        11: "DEF", 12: "DEF", 13: "DEF", 14: "DEF",
        21: "MID", 22: "MID", 23: "MID", 24: "MID",
        31: "FWD", 32: "FWD",
        # bench outfield candidates
        41: "DEF", 42: "MID", 43: "FWD",
    }


def _base_xi():
    # 1 GKP, 4 DEF, 4 MID, 2 FWD = 11, valid formation
    return {1, 11, 12, 13, 14, 21, 22, 23, 24, 31, 32}


def test_no_subs_needed_when_everyone_played():
    xi = _base_xi()
    minutes = {c: 90 for c in xi}
    result = apply_autosubs(xi, [41, 42, 43], 2, 31, 32, minutes, _positions())
    assert result.final_xi == xi
    assert result.subs_made == []


def test_zero_minute_starter_replaced_by_first_playing_bench():
    xi = _base_xi()
    minutes = {c: 90 for c in xi}
    minutes[32] = 0  # FWD didn't play
    minutes[41] = 90  # first bench (DEF) played
    minutes[42] = 0
    minutes[43] = 0
    result = apply_autosubs(xi, [41, 42, 43], 2, 31, 32, minutes, _positions())
    assert 32 not in result.final_xi
    assert 41 in result.final_xi
    assert (32, 41) in result.subs_made


def test_bench_player_who_also_has_zero_minutes_is_skipped():
    xi = _base_xi()
    minutes = {c: 90 for c in xi}
    minutes[32] = 0
    minutes[41] = 0  # also didn't play -> skip
    minutes[42] = 90  # this one played -> used instead
    minutes[43] = 90
    result = apply_autosubs(xi, [41, 42, 43], 2, 31, 32, minutes, _positions())
    assert 32 not in result.final_xi
    assert 42 in result.final_xi
    assert 41 not in result.final_xi


def test_sub_skipped_if_it_would_break_formation():
    # starting XI already at max DEF (5) is impossible with base_xi (4 DEF), but
    # we can construct a case where subbing in another DEF would exceed the
    # formation bound: use an XI with 5 DEF (max) and 1 FWD (min), then a
    # 0-minute FWD would need a non-DEF bench replacement to stay legal.
    xi = {1, 11, 12, 13, 14, 41, 21, 22, 23, 24, 31}  # 1 GKP, 5 DEF, 4 MID, 1 FWD = 11
    minutes = {c: 90 for c in xi}
    minutes[31] = 0  # the lone FWD didn't play; FWD min is 1, so we can't drop to 0 FWD
    positions = _positions()
    positions[99] = "DEF"
    minutes[99] = 90  # only bench option that played is another DEF
    result = apply_autosubs(xi, [99, 42, 43], 2, 11, 12, minutes, positions)
    # subbing DEF-for-FWD would leave 0 FWD (invalid, min 1) -> must not happen
    assert 31 in result.final_xi
    assert 99 not in result.final_xi


def test_goalkeeper_autosub_only_swaps_with_bench_gk():
    xi = _base_xi()
    minutes = {c: 90 for c in xi}
    minutes[1] = 0  # starting GK didn't play
    minutes[2] = 90  # bench GK played
    result = apply_autosubs(xi, [41, 42, 43], 2, 31, 32, minutes, _positions())
    assert 1 not in result.final_xi
    assert 2 in result.final_xi


def test_goalkeeper_not_subbed_if_bench_gk_also_zero_minutes():
    xi = _base_xi()
    minutes = {c: 90 for c in xi}
    minutes[1] = 0
    minutes[2] = 0
    result = apply_autosubs(xi, [41, 42, 43], 2, 31, 32, minutes, _positions())
    assert 1 in result.final_xi  # no valid replacement, stays (scores 0)


def test_captain_armband_transfers_to_vice_when_captain_blanks():
    xi = _base_xi()
    minutes = {c: 90 for c in xi}
    minutes[31] = 0  # captain didn't play
    result = apply_autosubs(xi, [41, 42, 43], 2, captain=31, vice_captain=32,
                             actual_minutes=minutes, position_by_code=_positions())
    assert result.effective_captain == 32


def test_captain_stays_captain_when_they_played():
    xi = _base_xi()
    minutes = {c: 90 for c in xi}
    result = apply_autosubs(xi, [41, 42, 43], 2, captain=31, vice_captain=32,
                             actual_minutes=minutes, position_by_code=_positions())
    assert result.effective_captain == 31
