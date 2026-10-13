"""Pre-deadline captaincy with the one-way mid-matchday switch priced in."""
import pytest

from uclscout.captaincy import (
    checklist_line,
    expected_max,
    plan_captaincy,
    points_sd,
)
from uclscout.feeds import Player
from uclscout.project import Minutes, MinutesSource, Projection
from uclscout.publish import _apply_captain

TUE, WED = "2026-10-13", "2026-10-14"


def _x(pid, pts, day, trusted=True):
    pl = Player(id=pid, name=f"P{pid}", team_id=pid, team="T", pos="FWD",
                value=9.0, status="", selected_pct=1, total_points=0,
                minutes=900, goals=0, assists=0, clean_sheets=0, saves=0,
                recoveries=0, motm=0, as_of_matchday=2)
    src = MinutesSource.OBSERVED_UCL if trusted else MinutesSource.IMPUTED_PRICE
    return Projection(pl, Minutes(85.0, src, p_start=0.95), 6.0, 1.0, pts, 1,
                      True, day)


def test_expected_max_matches_its_limits():
    assert expected_max(5.0, 1e-9, 3.0) == pytest.approx(5.0)
    assert expected_max(1.0, 1e-9, 3.0) == pytest.approx(3.0)
    # the option is worth something whenever there is spread
    assert expected_max(5.0, points_sd(5.0), 5.0) > 5.0 + 1.0


def test_early_captain_with_later_cover_beats_a_slightly_better_late_one():
    early, late = _x(1, 7.0, TUE), _x(2, 7.5, WED)
    cs = plan_captaincy([early, late, _x(3, 2.0, TUE)])
    assert cs.captain is early and cs.switch_to is late
    assert cs.switch_if_below == pytest.approx(7.5)
    assert cs.value > late.points


def test_among_early_players_the_bigger_upside_wins():
    cs = plan_captaincy([_x(1, 4.0, TUE), _x(2, 8.0, TUE), _x(3, 6.0, WED)])
    assert cs.captain.player.id == 2 and cs.switch_to.player.id == 3


def test_single_day_matchday_has_no_switch():
    cs = plan_captaincy([_x(1, 7.0, TUE), _x(2, 7.5, TUE)])
    assert cs.captain.player.id == 2
    assert cs.switch_to is None and cs.switch_if_below is None
    assert checklist_line(cs) is None


def test_imputed_minutes_are_neither_captain_nor_switch_target():
    cs = plan_captaincy([_x(1, 6.0, TUE), _x(2, 15.0, WED, trusted=False)])
    assert cs.captain.player.id == 1 and cs.switch_to is None


def test_checklist_line_states_the_integer_threshold():
    cs = plan_captaincy([_x(1, 7.0, TUE), _x(2, 6.0, WED)])
    line = checklist_line(cs)
    assert "P1 (Tue)" in line and "5 or fewer" in line and "P2" in line


def test_published_squad_shows_one_captain():
    xi = [_x(1, 7.0, TUE), _x(2, 7.5, WED)]
    cs = plan_captaincy(xi)
    squad = {"captainId": 2, "xiPoints": 14.5, "totalPoints": 22.0,
             "picks": [{"id": 1, "captain": False}, {"id": 2, "captain": True}]}
    _apply_captain(squad, cs, plan_first=None)
    assert squad["captainId"] == 1 == squad["captainSwitch"]["captainId"]
    assert squad["captainSwitch"]["switchTo"] == 2
    assert squad["captainSwitch"]["switchIfBelow"] == 7.5
    assert [p["id"] for p in squad["picks"] if p["captain"]] == [1]
    assert squad["totalPoints"] == pytest.approx(21.5)
