"""The live pipeline past MD8: generic knockout projections, eliminated clubs,
second-leg rotation and per-round rules in the plan. Offline, synthetic."""
import random
from datetime import UTC, datetime, timedelta

import pytest

from uclscout import feeds, knockout, pipeline, plan
from uclscout.feeds import Constraints, Match, Matchday, Player, Team

TEAMS = list(range(1, 37))
FUTURE = datetime.now(UTC) + timedelta(days=365)
SHAPE = (("GK", 2), ("DEF", 5), ("MID", 5), ("FWD", 3))


def _players(status=None):
    out, pid = [], 0
    for t in TEAMS:
        for pos, n in SHAPE:
            for k in range(n):
                pid += 1
                out.append(Player(
                    id=pid, name=f"P{pid}", team_id=t, team=f"T{t}", pos=pos,
                    value=4.5 + (k % 3) + (36 - t) * 0.05,
                    status=(status or {}).get(t, ""), selected_pct=1.0,
                    total_points=10 + (36 - t) // 3 + k, minutes=180,
                    goals=0, assists=0, clean_sheets=0, saves=0, recoveries=0,
                    motm=0, as_of_matchday=3))
    return out


def _md(md, pairs, scores=None, day=0):
    kick = FUTURE + timedelta(days=7 * md + day)
    scores = scores or {}
    return Matchday(md, kick - timedelta(hours=2), 2, False, False, [
        Match(1000 * md + i, md, kick, h, a, False, "",
              *scores.get((h, a), (None, None)))
        for i, (h, a) in enumerate(pairs)])


def _league(played_upto=0, seed=1):
    rng, mds = random.Random(seed), []
    for md in range(1, 9):
        pool = TEAMS[:]
        rng.shuffle(pool)
        pairs = list(zip(pool[0::2], pool[1::2]))
        scores = ({(h, a): ((1, 0) if h < a else (0, 1)) for h, a in pairs}
                  if md <= played_upto else None)
        mds.append(_md(md, pairs, scores))
    return mds


def _ctx(matchday, mds, *, players=None, eliminated=(), max_per_club=3,
         budget=100.0):
    teams = [Team(t, f"T{t}", f"Team {t}", 1 + (t - 1) // 9,
                  eliminated=t in eliminated) for t in TEAMS]
    c = Constraints(matchday, 17, budget, max_per_club, FUTURE)
    ctx = pipeline.Context(90, c, teams, mds, players or _players(), {}, set())
    ctx.survival = pipeline.estimate_survival(ctx, n_sims=400)
    return ctx


def _by_md(h):
    return {md: {p.player.id: p for p in projs} for md, projs in h.items()}


def test_horizon_runs_to_the_final_with_p_alive_weighting():
    ctx = _ctx(3, _league(played_upto=2))
    h = pipeline.horizon(ctx)
    assert sorted(h) == list(range(3, 18))
    by = _by_md(h)
    # a knockout matchday before the draw: points = generic projection x P(plays)
    surv = ctx.survival
    for md in (9, 11, 13, 17):
        for p in h[md][:25]:
            assert p.opponent_id is None and p.home is None
            assert 0 < surv.p_plays(p.player.team_id, md) <= 1
    # the strongest club's star is worth less at the final than at MD3, by
    # roughly P(final) -- and a weak club's far less
    star = next(p for p in h[3] if p.player.team_id == 1)
    weak = next(p for p in h[3] if p.player.team_id == 36)
    r_star = by[17][star.player.id].points / star.points
    final_weak = by[17].get(weak.player.id)        # absent: no chance at all
    r_weak = final_weak.points / weak.points if final_weak else 0.0
    assert r_star > 2 * r_weak
    assert r_star < 0.6


def test_no_survival_estimate_keeps_the_old_horizon():
    ctx = _ctx(3, _league(played_upto=2))
    ctx.survival = None
    assert sorted(pipeline.horizon(ctx)) == list(range(3, 9))


def test_a_failing_simulation_degrades_instead_of_raising(monkeypatch):
    def boom(*a, **k):
        raise KeyError("odd feed")
    monkeypatch.setattr(knockout, "simulate", boom)
    ctx = _ctx(7, _league(played_upto=6))
    assert ctx.survival is None
    p = pipeline.build_plan(ctx, time_limit=20)
    assert [a.matchday for a in p.actions] == [7, 8]


def test_eliminated_clubs_project_nothing():
    # club 2 flagged by the teams feed, club 3 by its players' "E" status
    ctx = _ctx(3, _league(played_upto=2), eliminated={2},
               players=_players(status={3: feeds.ELIMINATED}))
    assert {2, 3} <= ctx.eliminated
    h = pipeline.horizon(ctx)
    for md in range(9, 18):
        assert not any(p.player.team_id in (2, 3) for p in h.get(md, []))
    assert all(ctx.survival.p_plays(t, 11) == 0.0 for t in (2, 3))


def test_second_leg_lead_is_discounted():
    league = _league(played_upto=8)
    first = _md(9, [(17, 9), (18, 10)], {(17, 9): (0, 3), (18, 10): (1, 1)})
    second = _md(10, [(9, 17), (10, 18)])
    ctx = _ctx(10, league + [first, second])
    projs = pipeline.project_matchday(ctx, 10)
    lead = [p for p in projs if p.player.team_id == 9]
    level = [p for p in projs if p.player.team_id == 10]
    assert lead and level
    assert all("first-leg lead" in p.minutes.note for p in lead)
    assert not any("first-leg lead" in p.minutes.note for p in level)
    # the same players without the result in the feed: 7% more
    plain = pipeline.project_matchday(
        _ctx(10, league + [_md(9, [(17, 9), (18, 10)]), second]), 10)
    base = {p.player.id: p.points for p in plain}
    for p in lead:
        assert p.points == pytest.approx(base[p.player.id]
                                         * knockout.ROTATION_FACTOR)


def test_segments_split_at_the_free_rebuilds():
    assert pipeline.segments(list(range(3, 18))) == [
        list(range(3, 9)), [9, 10], list(range(11, 18))]
    assert pipeline.segments([12, 13, 14]) == [[12, 13, 14]]


def test_rules_per_matchday():
    ctx = _ctx(3, _league(played_upto=2))
    budgets, clubs = pipeline.rules_for(ctx, list(range(3, 18)), 99.5, 3)
    assert budgets[8] == 99.5 and budgets[9] == 104.5 and budgets[17] == 104.5
    assert [clubs[m] for m in (3, 9, 11, 13, 15, 17)] == [3, 4, 4, 5, 6, 8]


def test_quarter_final_plan_respects_round_rules():
    """Plan from a drawn QF: real fixtures for MD13-14, generic after."""
    league = _league(played_upto=8)
    qf = [(1, 2), (3, 4), (5, 6), (7, 8)]
    mds = league + [_md(13, qf), _md(14, [(a, h) for h, a in qf])]
    ctx = _ctx(13, mds, max_per_club=5, budget=105.0)
    # clubs outside the QF are out: flag them, as the feed would
    ctx.teams = [Team(t.id, t.short, t.name, t.pot, eliminated=t.id > 8)
                 for t in ctx.teams]
    ctx.survival = pipeline.estimate_survival(ctx, n_sims=300)
    h = pipeline.horizon(ctx)
    assert sorted(h) == [13, 14, 15, 16, 17]
    assert {p.player.team_id for p in h[13]} <= set(range(1, 9))
    p = pipeline.build_plan(ctx, time_limit=20)
    caps = {13: 5, 14: 5, 15: 6, 16: 6, 17: 8}
    for a in p.actions:
        clubs = [x.player.team_id for x in a.squad]
        assert len(a.squad) == 15
        assert max(clubs.count(c) for c in set(clubs)) <= caps[a.matchday]
        assert a.generic == (a.matchday >= 15)
        assert sum(x.player.value for x in a.squad) <= 105.0 + 1e-6
    assert plan.UNLIMITED_MATCHDAYS == knockout.UNLIMITED_MATCHDAYS
