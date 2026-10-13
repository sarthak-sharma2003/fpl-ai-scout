"""Knockout rules and the survival simulation. Offline, synthetic."""
import random

import pytest

from uclscout import knockout
from uclscout.knockout import Fixture, first_leg_margin, simulate

TEAMS = list(range(1, 37))
RATING = {t: 1800.0 - 10 * t for t in TEAMS}       # club 1 strongest


def _league(played=False, seed=1):
    rng, out = random.Random(seed), []
    for md in range(1, 9):
        pool = TEAMS[:]
        rng.shuffle(pool)
        for h, a in zip(pool[0::2], pool[1::2]):
            if played:          # the stronger club wins by the rating gap
                g = (abs(h - a), 0) if h < a else (0, abs(h - a))
                out.append(Fixture(md, h, a, *g))
            else:
                out.append(Fixture(md, h, a))
    return out


@pytest.mark.parametrize("md,round_,cap,free,carry", [
    (2, "league", 3, 2, True), (8, "league", 3, 2, True),
    (9, "playoff", 4, None, False), (10, "playoff", 4, 2, False),
    (11, "r16", 4, None, False), (12, "r16", 4, 3, False),
    (13, "qf", 5, 5, False), (14, "qf", 5, 3, False),
    (15, "sf", 6, 5, False), (16, "sf", 6, 3, False),
    (17, "final", 8, 5, False),
])
def test_round_rules(md, round_, cap, free, carry):
    assert knockout.round_of(md) == round_
    assert knockout.max_per_club(md) == cap
    assert knockout.free_transfers(md) == free
    assert knockout.carries(md) is carry


def test_budget_rises_once_at_the_knockouts():
    assert knockout.budget_for(8, 100.0, 2) == 100.0
    assert knockout.budget_for(9, 100.0, 2) == 105.0
    assert knockout.budget_for(17, 100.0, 8) == 105.0
    # funds measured in the knockouts already include the rise
    assert knockout.budget_for(13, 104.2, 12) == 104.2


def test_field_halves_each_round():
    s = simulate(TEAMS, _league(), RATING.get, n_sims=600, seed=2)
    for md, n in ((9, 16), (10, 16), (11, 16), (12, 16), (13, 8), (14, 8),
                  (15, 4), (16, 4), (17, 2)):
        assert sum(s.p_plays(t, md) for t in TEAMS) == pytest.approx(n), md
    assert sum(s.top8.values()) == pytest.approx(8)
    # a top-8 finish is a bye: P(play-off) + P(top 8) <= 1 for every club
    for t in TEAMS:
        assert s.p_plays(t, 9) + s.top8[t] <= 1 + 1e-9
    assert s.p_plays(1, 17) > s.p_plays(20, 17) > s.p_plays(36, 17)


def test_drawn_pairings_and_results_are_fact():
    league = _league(played=True)
    # the play-offs are drawn: 9 ties' worth of clubs, and one first leg played
    s0 = simulate(TEAMS, league, RATING.get, n_sims=50, seed=3)
    playoff = [t for t in TEAMS if s0.p_plays(t, 9) == 1.0]
    assert len(playoff) == 16
    ties = [Fixture(9, playoff[i], playoff[15 - i]) for i in range(8)]
    ties[0] = Fixture(9, ties[0].home_id, ties[0].away_id, 0, 5)
    seconds = [Fixture(10, f.away_id, f.home_id) for f in ties]
    s = simulate(TEAMS, league + ties + seconds, RATING.get, n_sims=400, seed=4)
    for t in playoff:
        assert s.p_plays(t, 9) == 1.0 and s.p_plays(t, 10) == 1.0
    others = set(TEAMS) - set(playoff)
    assert all(s.p_plays(t, 9) == 0.0 for t in others)
    # 0-5 down after the first leg away... at home: still possible, unlikely
    loser = ties[0].home_id
    assert s.p_plays(loser, 11) < 0.15


def test_eliminated_flag_zeroes_every_later_round():
    s = simulate(TEAMS, _league(), RATING.get, n_sims=300, seed=5,
                 eliminated={1})
    assert all(s.p_plays(1, md) == 0.0 for md in range(9, 18))
    assert s.p_plays(1, 3) == 1.0          # league fixtures are still fixtures


def test_small_field_degrades_to_published_fixtures():
    fx = [Fixture(1, 1, 2), Fixture(9, 1, 2)]
    s = simulate([1, 2], fx, lambda t: 1500.0)
    assert s.p_plays(1, 9) == 1.0 and s.p_plays(1, 11) == 0.0
    assert "not simulated" in s.note


def test_first_leg_margin():
    fx = [Fixture(11, 5, 6, 0, 4), Fixture(12, 6, 5)]
    assert first_leg_margin(fx, 6, 12) == 4
    assert first_leg_margin(fx, 5, 12) == -4
    assert first_leg_margin(fx, 5, 11) is None        # not a second leg
    assert first_leg_margin([Fixture(11, 5, 6), Fixture(12, 6, 5)], 5, 12) is None
