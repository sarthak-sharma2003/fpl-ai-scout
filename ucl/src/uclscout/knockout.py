"""The knockout phase: round structure, per-round rules, and who is still alive.

From MD9 the field halves repeatedly and a club that is out scores zero for the
rest of the season. The archive replay measured "is this club still playing" as
the single largest signal in the game (docs/GAPS.md #5), so this module answers
two questions for every club and every remaining matchday:

  * does he have a fixture at all?  P(plays at md), from a Monte Carlo of the
    rest of the season on the same Elo the projections use;
  * against whom, roughly?  E[opponent rating | plays], so a knockout matchday
    whose bracket is not drawn yet can still be projected against a generic
    opponent of the right strength.

FORMAT (2024-25 onward, verified against the 2024-25 and 2025-26 fixture feeds)
  MD1-8    league phase, 36 clubs, one table. 1-8 go straight to the R16 (and so
           have NO fixture at MD9-10), 9-24 play the knockout play-offs, 25-36
           are out.
  MD9-10   play-offs, two legs: 9/10 v 23/24, 11/12 v 21/22, 13/14 v 19/20,
           15/16 v 17/18, the seeded club at home in the second leg.
  MD11-12  R16: 1/2 v the play-off winners from the 15/16 ties, 3/4 v 13/14,
           5/6 v 11/12, 7/8 v 9/10; the league-phase club at home second.
  MD13-14  QF, MD15-16 SF, MD17 the final (one match, neutral venue).

ASSUMPTIONS, each a deliberate simplification:
  * Goals are Poisson from `strength.expected_goals`, the same Elo -> goals map
    the rest of the package uses; ratings do not update inside a simulation.
  * League ties break on points, goal difference, goals scored, then a coin --
    UEFA's later tie-breakers (away goals, wins, opponents' points) are not
    modelled; they decide a handful of places a season.
  * A tie level on aggregate is a coin flip (extra time and penalties).
  * The real bracket from the QF on is fixed by the R16 draw; the feed does not
    expose it, so QF and SF opponents are paired at random among survivors.
    This blurs WHO a club meets, not WHETHER it plays.
  * Whatever the feed has published is used as fact: played scores, drawn
    pairings, `isEliminated`. Simulation fills only what is still unknown.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from . import strength

LAST_LEAGUE_MD = 8
FINAL_MD = 17
# (round, first-leg matchday, second-leg matchday or None)
STAGES = (("playoff", 9, 10), ("r16", 11, 12), ("qf", 13, 14),
          ("sf", 15, 16), ("final", 17, None))
_ROUND_OF = {md: name for name, a, b in STAGES for md in (a, b) if md}
SECOND_LEGS = {b: a for _, a, b in STAGES if b}          # second leg -> first

# --------------------------------------------------------------------------
# per-round rules (docs/GAPS.md #7)

# Everyone gets a free rebuild before these, so chips are barred on them too.
UNLIMITED_MATCHDAYS = frozenset({1, 9, 11})
FREE_PER_MATCHDAY = 2          # league phase; at most one carries
MAX_CARRY = 1
BUDGET_LEAGUE = 100.0
KO_BUDGET_RISE = 5.0           # EUR100m -> EUR105m from the knockouts onward
MAX_PER_CLUB = {"league": 3, "playoff": 4, "r16": 4, "qf": 5, "sf": 6,
                "final": 8}
# Free transfers before each knockout matchday that is not an unlimited window.
# UNVERIFIED as rules text: secondary sources say "5 before the QF and the
# final, 3 before second legs". The fixtures feed of BOTH completed seasons in
# this format agrees and fills the gaps: `subsAllowed` reads 2 (MD10), 3 (MD12,
# MD14, MD16) and 5 (MD13, MD15, MD17), with `maxSubsCf` (the carry cap) 1 in
# the league phase and 0 in every knockout matchday -- nothing rolls over in
# the knockouts. (The same field reads 5 for MD9/MD11 and 2 for MD1, which are
# unlimited windows, so it is a cross-check here, not a source of truth.)
KO_FREE_TRANSFERS = {10: 2, 12: 3, 13: 5, 14: 3, 15: 5, 16: 3, 17: 5}

# Second legs (docs/GAPS.md #8). A club ROTATION_LEAD+ goals up after the first
# leg has its whole projection scaled by ROTATION_FACTOR. Measured on the
# archive (2024-25 and 2025-26, every two-legged tie; "regulars" = scored 2+
# in the first leg): of 142 regulars at the 13 clubs 3+ up, 27.5% scored under
# 2 in the second leg, against 22.4% at clubs within two goals either way --
# about 7% fewer starts. Their POINTS per regular did not drop (1.19x their
# league-phase average vs 1.13-1.17x in close ties), so this stays a small,
# conservative haircut, not a rotation model; 13 ties is noise-level evidence.
# The trailing side is left alone: "throws everything forward" is folklore the
# same data does not support (3+ down: 0.81x, the lowest of any bucket).
ROTATION_LEAD = 3
ROTATION_FACTOR = 0.93


def round_of(md: int) -> str:
    return "league" if md <= LAST_LEAGUE_MD else _ROUND_OF.get(md, "final")


def is_knockout(md: int) -> bool:
    return md > LAST_LEAGUE_MD


def max_per_club(md: int) -> int:
    return MAX_PER_CLUB[round_of(md)]


def free_transfers(md: int) -> int | None:
    """Free transfers going into `md` before any carry; None = unlimited."""
    if md in UNLIMITED_MATCHDAYS:
        return None
    if md <= LAST_LEAGUE_MD:
        return FREE_PER_MATCHDAY
    return KO_FREE_TRANSFERS.get(md, FREE_PER_MATCHDAY)


def carries(md: int) -> bool:
    """Can an unused transfer roll INTO `md`? Only in the league phase."""
    return md <= LAST_LEAGUE_MD


def budget_for(md: int, funds: float, funds_md: int) -> float:
    """Spending power at `md`, given `funds` measured going into `funds_md`.

    The knockout rise is added once, to matchdays past the league phase, and
    only when the funds were measured before it (a held squad's value plus bank
    already includes the rise once the knockouts have started).
    """
    if funds_md <= LAST_LEAGUE_MD < md:
        return funds + KO_BUDGET_RISE
    return funds


# --------------------------------------------------------------------------
# fixtures

@dataclass(frozen=True)
class Fixture:
    matchday: int
    home_id: int
    away_id: int
    home_goals: int | None = None      # None until played
    away_goals: int | None = None

    @property
    def played(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None


def fixtures_from(matchdays: Iterable) -> list[Fixture]:
    """`feeds.Matchday` list -> flat fixtures (scores where the feed has them)."""
    out = []
    for md in matchdays:
        for m in md.matches:
            out.append(Fixture(md.id, m.home_id, m.away_id,
                               getattr(m, "home_goals", None),
                               getattr(m, "away_goals", None)))
    return out


def first_leg_margin(fixtures: Iterable[Fixture], team_id: int,
                     md: int) -> int | None:
    """Goals `team_id` led by after the first leg, when `md` is a second leg.

    Read from the first-leg RESULT, not from `htAggScore`/`atAggScore`: those
    are populated in 2024-25 second legs but read "0"/"0" throughout 2025-26,
    so they cannot be trusted to mean anything.
    """
    first = SECOND_LEGS.get(md)
    if first is None:
        return None
    for f in fixtures:
        if f.matchday != first or team_id not in (f.home_id, f.away_id):
            continue
        if not f.played:
            return None
        own, opp = ((f.home_goals, f.away_goals) if f.home_id == team_id
                    else (f.away_goals, f.home_goals))
        return int(own) - int(opp)
    return None


# --------------------------------------------------------------------------
# simulation

@dataclass
class Survival:
    """P(club has a fixture at md), with the opponent strength it implies."""
    plays: dict[int, dict[int, float]] = field(default_factory=dict)
    opp_rating: dict[int, dict[int, float]] = field(default_factory=dict)
    top8: dict[int, float] = field(default_factory=dict)
    n_sims: int = 0
    note: str = ""

    def p_plays(self, team_id: int, md: int) -> float:
        return self.plays.get(team_id, {}).get(md, 0.0)

    def p_round(self, team_id: int) -> dict[str, float]:
        """P(reaching each knockout round), for publishing."""
        row = self.plays.get(team_id, {})
        out = {"top8": round(self.top8.get(team_id, 0.0), 3)}
        for name, first, _ in STAGES:
            out[name] = round(row.get(first, 0.0), 3)
        return out


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _lambdas(r_home: float, r_away: float, neutral: bool = False
             ) -> tuple[float, float]:
    if neutral:
        d = r_home - r_away
        return (max(0.25, strength.MEAN_GOALS + strength.GOALS_PER_ELO * d),
                max(0.25, strength.MEAN_GOALS - strength.GOALS_PER_ELO * d))
    return (strength.expected_goals(r_home, r_away, home=True),
            strength.expected_goals(r_away, r_home, home=False))


def simulate(
    team_ids: Iterable[int],
    fixtures: list[Fixture],
    rating: Callable[[int], float],
    *,
    eliminated: Iterable[int] = (),
    n_sims: int = 2000,
    seed: int = 2027,
) -> Survival:
    """Monte Carlo of the rest of the season, from whatever is already known.

    `fixtures` may hold any mix of played and unplayed matches; knockout
    matchdays present in it are taken as the drawn pairings for that round.
    Clubs in `eliminated` lose every tie they would otherwise reach.
    """
    teams = sorted(set(team_ids))
    out = Survival(n_sims=n_sims)
    league = [f for f in fixtures if f.matchday <= LAST_LEAGUE_MD]
    for f in league:                       # league fixtures are certain
        for t in (f.home_id, f.away_id):
            out.plays.setdefault(t, {})[f.matchday] = 1.0
    if len(teams) < 24:
        # not this format: only what the feed has drawn is known
        for f in fixtures:
            for t in (f.home_id, f.away_id):
                out.plays.setdefault(t, {})[f.matchday] = 1.0
        out.note = f"only {len(teams)} clubs: knockout format not simulated"
        return out
    rate = {t: float(rating(t)) for t in teams}
    dead = set(eliminated)
    ko: dict[int, list[Fixture]] = defaultdict(list)
    for f in fixtures:
        if f.matchday > LAST_LEAGUE_MD:
            ko[f.matchday].append(f)

    base_pts = dict.fromkeys(teams, 0)
    base_gd = dict.fromkeys(teams, 0)
    base_gf = dict.fromkeys(teams, 0)
    todo = []
    for f in league:
        if f.home_id not in rate or f.away_id not in rate:
            continue
        if f.played:
            _book(base_pts, base_gd, base_gf, f.home_id, f.away_id,
                  int(f.home_goals), int(f.away_goals))
        else:
            todo.append((f.home_id, f.away_id,
                         *_lambdas(rate[f.home_id], rate[f.away_id])))

    rng = random.Random(seed)
    hits: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    opp: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    top8: dict[int, float] = defaultdict(float)
    for _ in range(n_sims):
        pts, gd, gf = dict(base_pts), dict(base_gd), dict(base_gf)
        for h, a, lh, la in todo:
            _book(pts, gd, gf, h, a, _poisson(rng, lh), _poisson(rng, la))
        # a club the feed already calls eliminated sinks to the bottom: the
        # flag is fact, our tie-breakers are an approximation
        order = sorted(teams, key=lambda t: (t in dead, -pts[t], -gd[t],
                                             -gf[t], rng.random()))
        rank = {t: i + 1 for i, t in enumerate(order)}
        for t in order[:8]:
            top8[t] += 1
        _knockouts(order, rank, ko, rate, dead, rng, hits, opp)

    for t in teams:
        row = out.plays.setdefault(t, {})
        for md in range(LAST_LEAGUE_MD + 1, FINAL_MD + 1):
            n = hits[t].get(md, 0.0)
            row[md] = n / n_sims
            if n:
                out.opp_rating.setdefault(t, {})[md] = opp[t][md] / n
        out.top8[t] = top8[t] / n_sims
    out.note = (f"{n_sims} simulations; {len(todo)} league matches and "
                f"{sum(1 for md in ko for f in ko[md] if not f.played)} "
                "drawn knockout matches still to play")
    return out


def _book(pts, gd, gf, h, a, gh, ga):
    pts[h] += 3 if gh > ga else 1 if gh == ga else 0
    pts[a] += 3 if ga > gh else 1 if gh == ga else 0
    gd[h] += gh - ga
    gd[a] += ga - gh
    gf[h] += gh
    gf[a] += ga


def _leg(rng, rate, fx: dict, md: int, home: int, away: int,
         neutral: bool = False) -> tuple[int, int]:
    """Goals (home, away) in one knockout match: the result if played."""
    f = fx.get((md, home, away))
    if f is not None and f.played:
        return int(f.home_goals), int(f.away_goals)
    f = fx.get((md, away, home))
    if f is not None and f.played:           # feed lists it the other way
        return int(f.away_goals), int(f.home_goals)
    lh, la = _lambdas(rate.get(home, 1500.0), rate.get(away, 1500.0), neutral)
    return _poisson(rng, lh), _poisson(rng, la)


def _knockouts(order, rank, ko, rate, dead, rng, hits, opp) -> None:
    fx = {(f.matchday, f.home_id, f.away_id): f
          for md in ko for f in ko[md]}
    survivors: list[int] = []
    bands: dict[int, int] = {}             # play-off winner -> R16 seed band
    for name, first, second in STAGES:
        drawn = ko.get(first)
        if drawn:
            ties = [(f.home_id, f.away_id) for f in drawn]
        elif name == "playoff":
            ties = []
            seeded, unseeded = order[8:16], order[16:24]
            for b in range(4):
                s_pair = seeded[2 * b:2 * b + 2]
                u_pair = unseeded[6 - 2 * b:8 - 2 * b]
                rng.shuffle(u_pair)
                for s, u in zip(s_pair, u_pair):
                    ties.append((u, s))     # unseeded club hosts the first leg
        elif name == "r16":
            ties = _r16_ties(order[:8], survivors, bands, rng)
        else:
            pool = list(survivors)
            rng.shuffle(pool)
            ties = list(zip(pool[0::2], pool[1::2]))
        winners = []
        for h, a in ties:
            for t, o in ((h, a), (a, h)):
                for md in (first, second):
                    if md:
                        hits[t][md] += 1
                        opp[t][md] += rate.get(o, 1500.0)
            if h in dead or a in dead:
                w = a if h in dead else h
            elif second is None:
                gh, ga = _leg(rng, rate, fx, first, h, a, neutral=True)
                w = h if gh > ga else a if ga > gh else rng.choice((h, a))
            else:
                g1h, g1a = _leg(rng, rate, fx, first, h, a)
                g2a, g2h = _leg(rng, rate, fx, second, a, h)
                th, ta = g1h + g2h, g1a + g2a
                w = h if th > ta else a if ta > th else rng.choice((h, a))
            winners.append(w)
            if name == "playoff":
                better = min(rank.get(h, 99), rank.get(a, 99))
                if 9 <= better <= 16:
                    bands[w] = (better - 9) // 2   # 0 = seeds 9/10
        survivors = winners


def _r16_ties(top8: list[int], playoff_winners: list[int],
              bands: dict[int, int], rng: random.Random) -> list[tuple[int, int]]:
    """League top 8 v play-off winners, by seeding band where it is known."""
    by_band: dict[int, list[int]] = defaultdict(list)
    for w in playoff_winners:
        by_band[bands.get(w, -1)].append(w)
    ties = []
    leftovers_top, leftovers_w = [], list(by_band.get(-1, []))
    for k in range(4):
        seeds = top8[2 * k:2 * k + 2]
        # seeds 1/2 meet the winners of the 15/16 ties (band 3), 7/8 band 0
        ws = list(by_band.get(3 - k, []))
        rng.shuffle(ws)
        for s in seeds:
            if ws:
                ties.append((ws.pop(), s))  # play-off winner hosts leg one
            else:
                leftovers_top.append(s)
        leftovers_w += ws
    rng.shuffle(leftovers_w)
    for s, w in zip(leftovers_top, leftovers_w):
        ties.append((w, s))
    return ties


def demo():
    """Self-check on a synthetic 36-club season."""
    teams = list(range(1, 37))
    rating = {t: 1800.0 - 10 * t for t in teams}           # 1 strongest
    fixtures, rng = [], random.Random(1)
    for md in range(1, 9):
        pool = teams[:]
        rng.shuffle(pool)
        for h, a in zip(pool[0::2], pool[1::2]):
            fixtures.append(Fixture(md, h, a))
    s = simulate(teams, fixtures, rating.get, n_sims=1500, seed=3)
    # every club plays every league matchday
    assert all(s.p_plays(t, 5) == 1.0 for t in teams)
    # exactly 16 clubs play each play-off leg, 16 the R16, 8 the QF, 2 the final
    for md, n in ((9, 16), (10, 16), (11, 16), (12, 16), (13, 8), (15, 4),
                  (17, 2)):
        tot = sum(s.p_plays(t, md) for t in teams)
        assert abs(tot - n) < 1e-6, (md, tot)
    # the strongest club is likelier to reach every round than the weakest
    for md in (11, 13, 15, 17):
        assert s.p_plays(1, md) > s.p_plays(36, md), md
    # a top-8 club mostly skips the play-offs, a mid-table one mostly plays them
    assert s.p_plays(1, 9) < s.p_plays(18, 9)

    # Once the league is played, the table is fact: no randomness left in who
    # is out, and an eliminated flag zeroes every later round.
    def result(h, a):            # the stronger club wins by the rating gap
        return (abs(h - a), 0) if h < a else (0, abs(h - a))
    played = [Fixture(f.matchday, f.home_id, f.away_id,
                      *result(f.home_id, f.away_id)) for f in fixtures]
    s2 = simulate(teams, played, rating.get, n_sims=300, seed=4,
                  eliminated={2})
    # (only clubs level on points, GD and goals at a cut-off stay uncertain)
    certain = sum(1 for t in teams if s2.p_plays(t, 9) in (0.0, 1.0))
    assert certain >= 30, certain
    assert abs(sum(s2.p_plays(t, 9) for t in teams) - 16) < 1e-9
    assert all(s2.p_plays(2, md) == 0.0 for md in (13, 15, 17)), s2.plays[2]

    # first-leg margins come from the result, from either side
    fx = [Fixture(9, 5, 6, 3, 0), Fixture(10, 6, 5)]
    assert first_leg_margin(fx, 5, 10) == 3
    assert first_leg_margin(fx, 6, 10) == -3
    assert first_leg_margin(fx, 5, 9) is None

    # rules
    assert [max_per_club(md) for md in (8, 9, 12, 13, 15, 17)] == [3, 4, 4, 5, 6, 8]
    assert free_transfers(9) is None and free_transfers(12) == 3
    assert budget_for(9, 100.0, 3) == 105.0 and budget_for(9, 105.0, 9) == 105.0
    assert budget_for(8, 100.0, 3) == 100.0
    print("knockout.py self-check OK — P(final) strongest "
          f"{s.p_plays(1, 17):.2f} vs weakest {s.p_plays(36, 17):.3f}")


if __name__ == "__main__":
    demo()
