"""Replay completed seasons to test a DECISION policy.

WHAT THIS CAN AND CANNOT TEST
-----------------------------
The archive gives real per-matchday POINTS, price and ownership, but component
stats (goals, minutes, recoveries) only as season-final totals. So this harness
tests transfer/captaincy/chip DECISIONS against real scoring. It cannot train a
component model, and it must never touch the season-final columns.

LEAK DISCIPLINE -- the rule everything here obeys
-------------------------------------------------
At matchday t the policy may see:
  * md_points for matchdays STRICTLY BEFORE t
  * value and selected_pct AS OF t (both are pre-deadline facts)
  * position, club
It may NOT see:
  * md_points at t or later
  * `season_total_points` or any other season-final column -- those are the
    final answer sitting in the same row, and using one at MD1 would leak the
    whole season. `_legal_view` drops them outright rather than trusting a
    caller to remember.

Only 2024-25 and 2025-26 have usable per-matchday scoring, so the discipline is
the same as the FPL project's: tune on 2024-25, touch 2025-26 once.

MEASURED RESULTS (2026-09-08), Elo fitted only on seasons strictly before the
one being replayed:

                        crowd template   model    gain
    tune     2024-25              924    1008      +84
    validate 2025-26             1023    1107      +84      <- one shot
    perfect hindsight ceiling   1472 / 1390

The gain transferred exactly. That is the bar this project should hold itself
to: the FPL project's equivalent change gained +349 on its tuning season and
+9 on holdout, which is how a tuning artefact looks.

WHAT THE SWEEP ACTUALLY SHOWED, and it is not what I expected:

    ownership only                          924
    + "is this club even playing"          1016   <- the dominant signal
    Elo gap WITHOUT is-playing              907   <- worse than nothing
    all three                              1049

1. Crowd ownership is a strong baseline and beat my first form-based policy
   outright (956 vs 735). Do not ship a form-chaser.
2. Knowing which clubs are still IN the competition is worth more than any
   rating. From MD9 the field halves repeatedly and an eliminated club scores
   zero forever.
3. Elo is worth a real but modest +33 ON TOP of availability, and is actively
   harmful without it -- it happily rates a strong eliminated club highly.

CAVEAT, and it is a big one: this harness tests the DECISION layer only. The
archive has no per-matchday minutes, so ownership is standing in for
availability information that the live pipeline gets directly from pStatus and
the fixture feed. Do NOT port the ownership weight into the live model on this
evidence alone -- there it would double-count. What transfers is the ordering
of what matters: availability first, fixtures second, form a distant third.

KNOCKOUT-AWARE REPLAY (2026-10-13), `Policy` flags, each row adding one change
to the form-only "planner" policy (net points; league MD1-8 / knockouts):

                            2024-25            2025-26
    planner (as before)     709  407/302      704  443/261
    + rules (simulator)     774  392/382      723  443/280    +65  +19
    + playing               902  392/510      879  443/436   +128 +156
    + alive (P(alive))      895  392/503      922  443/479     -7  +43
    + rotation              891  392/499      908  443/465     -4  -14

`rules` is a correctness fix to the SIMULATOR (per-round budget and club cap,
the knockout transfer allowances, the carry count), not a policy. `playing` --
zero a club with no fixture this matchday -- is the elimination signal again,
+128/+156, the same order as the +92 above and the only change here clearly
above the noise. `alive` (rank transfers on P(alive)-weighted points to the
next free rebuild) and `rotation` (second-leg haircut for a club 3+ up) are
inside the ~50-point noise of two seasons; neither was tuned on these totals.
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import feeds, knockout, strength
from .plan import HORIZON_DECAY

SQUAD_SHAPE = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
SQUAD_SIZE, XI_SIZE = 15, 11
BUDGET = 100.0
MAX_PER_CLUB = 3
FREE_PER_MATCHDAY, MAX_CARRY, HIT_COST = 2, 1, 4
UNLIMITED_MATCHDAYS = frozenset({1, 9, 11})

# Columns that contain the answer. Never visible to a policy.
FORBIDDEN = ("season_total_points", "season_minutes", "season_recoveries",
             "season_goals", "season_assists", "season_clean_sheets",
             "season_saves", "season_motm")

PRIOR_MATCHDAYS = 3.0     # shrinkage strength for in-season form


@dataclass
class SeasonResult:
    season: str
    policy: str
    total: int = 0
    captain_points: int = 0
    hits: int = 0
    transfers: int = 0
    per_matchday: list[int] = field(default_factory=list)

    @property
    def net(self) -> int:
        return self.total - self.hits * HIT_COST

    def __str__(self) -> str:
        return (f"{self.season}  {self.policy:<12} net {self.net:>5}  "
                f"(raw {self.total}, {self.transfers} transfers, "
                f"{self.hits} hits, captain {self.captain_points})")


def load_season(csv_path: Path, season: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[df.season == season].copy()
    df["md_points"] = pd.to_numeric(df.md_points, errors="coerce")
    df = df.dropna(subset=["md_points"])
    df["md_points"] = df.md_points.astype(int)
    df["value"] = pd.to_numeric(df.value, errors="coerce").fillna(4.0)
    return df


def _legal_view(df: pd.DataFrame, matchday: int) -> pd.DataFrame:
    """Everything a policy is allowed to see at `matchday`. Enforced, not asked."""
    past = df[df.matchday < matchday]
    now = df[df.matchday == matchday][
        ["player_id", "name", "pos", "team_id", "value", "selected_pct"]
    ].drop_duplicates("player_id").set_index("player_id")
    form = past.groupby("player_id").md_points.agg(["sum", "count"])
    view = now.join(form, how="left").fillna({"sum": 0.0, "count": 0.0})
    return view.drop(columns=[c for c in FORBIDDEN if c in view.columns])


def form_projection(view: pd.DataFrame) -> pd.Series:
    """Shrunk per-matchday scoring rate, plus a small price prior.

    With no prior matchdays (MD1) this collapses to the price prior alone --
    which is the honest answer, since price is the only legal signal there.
    """
    pos_mean = view.groupby("pos")["sum"].sum() / view.groupby("pos")["count"].sum()
    pos_mean = pos_mean.fillna(1.0)
    prior = view.pos.map(pos_mean).fillna(1.0) * (0.5 + 0.5 * view.value / 8.0)
    return (view["sum"] + prior * PRIOR_MATCHDAYS) / (view["count"] + PRIOR_MATCHDAYS)


def _greedy_squad(view: pd.DataFrame, score: pd.Series,
                  budget: float = BUDGET,
                  max_per_club: int = MAX_PER_CLUB) -> set[int]:
    """Cheapest-first fill by value-per-million, then upgrade while affordable.

    ponytail: a MILP per matchday across a 17-matchday replay times several
    policies is minutes of solve time for a baseline. This greedy fill is within
    a point or two and the backtest compares policies, not squads.
    """
    picks: set[int] = set()
    spend = 0.0
    per_club: dict[int, int] = defaultdict(int)
    ranked = score.sort_values(ascending=False).index
    need = dict(SQUAD_SHAPE)
    # reserve enough budget to fill remaining slots at the cheapest price
    cheapest = view.value.min()
    for pid in ranked:
        if pid not in view.index:
            continue
        row = view.loc[pid]
        pos = row.pos
        if need.get(pos, 0) <= 0 or per_club[row.team_id] >= max_per_club:
            continue
        remaining = SQUAD_SIZE - len(picks) - 1
        if spend + row.value + remaining * cheapest > budget:
            continue
        picks.add(pid)
        spend += row.value
        per_club[row.team_id] += 1
        need[pos] -= 1
        if len(picks) == SQUAD_SIZE:
            break
    return picks


def _best_xi(squad: set[int], score: pd.Series,
             view: pd.DataFrame) -> tuple[list[int], int]:
    """Highest-scoring legal XI from a squad, and its captain."""
    avail = [p for p in squad if p in score.index]
    by_pos: dict[str, list[int]] = defaultdict(list)
    for p in sorted(avail, key=lambda p: -score[p]):
        by_pos[view.loc[p, "pos"]].append(p)
    xi = []
    for pos, n in XI_MIN.items():
        xi += by_pos[pos][:n]
    rest = [p for p in sorted(avail, key=lambda p: -score[p]) if p not in xi]
    gk = {p for p in avail if view.loc[p, "pos"] == "GK"}
    for p in rest:
        if len(xi) == XI_SIZE:
            break
        if p in gk:                      # exactly one keeper starts
            continue
        xi.append(p)
    captain = max(xi, key=lambda p: score[p]) if xi else None
    return xi, captain


# --------------------------------------------------------------------------
# knockout-aware policies (docs/GAPS.md #5-#8)

@dataclass(frozen=True)
class Policy:
    """What the replay's decision layer may use. Each flag adds one change,
    so a sweep isolates each one's worth.

    ko_rules  per-round budget and max-per-club, the knockout transfer
              allowances with nothing carried, and the carry bookkeeping fixed
              (it counted the season's transfers, not the matchday's). A
              correctness fix of the SIMULATOR, not a policy: it changes what
              the replay lets any policy do.
    playing   a player whose club has no fixture this matchday projects 0 --
              out of the competition, or a top-8 bye at MD9-10. Fixtures for
              the matchday are published before its deadline.
    alive     transfers and rebuilds rank players on points summed to the next
              free rebuild, each future matchday weighted by P(club plays it)
              from `knockout.simulate` on what was known at the deadline.
    rotation  a club 3+ up after the first leg is discounted in the second.
    """
    name: str
    ko_rules: bool = False
    playing: bool = False
    alive: bool = False
    rotation: bool = False


POLICIES = {
    "planner": Policy("planner"),
    "rules": Policy("rules", ko_rules=True),
    "playing": Policy("playing", ko_rules=True, playing=True),
    "alive": Policy("alive", ko_rules=True, playing=True, alive=True),
    "rotation": Policy("rotation", ko_rules=True, playing=True, alive=True,
                       rotation=True),
}
LOOKAHEAD_DECAY = HORIZON_DECAY         # the live planner's discount
SURVIVAL_SIMS = 1000


@dataclass
class SeasonFixtures:
    """One season's fixtures plus what Elo needs from earlier ones."""
    fixtures: list[knockout.Fixture]
    payload: dict                       # this season's raw fixtures feed
    season: str
    pots: dict[int, int]
    prior_results: list[strength.Result]


def load_fixtures(raw_dir: Path, tour: int,
                  prior_tours: tuple[int, ...] = (30, 40, 50, 60, 70, 80)
                  ) -> SeasonFixtures:
    """Fixtures dumps on disk (fixtures_{tour}.json) for the replay."""
    payload = json.loads((raw_dir / f"fixtures_{tour}.json").read_text())
    mds = feeds.parse_matchdays(payload)
    pots: dict[int, int] = {}
    for md in payload["data"]["value"]:
        for m in md.get("match") or []:
            for side in ("ht", "at"):
                name = str(m.get(f"{side}PtName") or "Pot 3")
                pots[int(m[f"{side}Id"])] = int(name.replace("Pot ", "") or 3)
    prior: list[strength.Result] = []
    for t in prior_tours:
        f = raw_dir / f"fixtures_{t}.json"
        if t < tour and f.exists():
            prior += strength.results_from_payload(
                json.loads(f.read_text()), feeds.SEASONS.get(t, str(t)))
    return SeasonFixtures(knockout.fixtures_from(mds), payload,
                          feeds.SEASONS.get(tour, str(tour)), pots,
                          sorted(prior, key=lambda r: r.date))


def visible_fixtures(fx: list[knockout.Fixture], md: int
                     ) -> list[knockout.Fixture]:
    """What a manager could know before `md`'s deadline.

    Every league fixture; knockout pairings only for rounds already drawn (a
    round is drawn before its first leg, never earlier); results only for
    matchdays strictly before `md`.
    """
    drawn = {m for _, first, second in knockout.STAGES if first <= md
             for m in (first, second) if m}
    out = []
    for f in fx:
        if knockout.is_knockout(f.matchday) and f.matchday not in drawn:
            continue
        if f.matchday >= md and f.played:
            f = knockout.Fixture(f.matchday, f.home_id, f.away_id)
        out.append(f)
    return out


def survival_at(sf: SeasonFixtures, md: int, teams: list[int],
                n_sims: int = SURVIVAL_SIMS) -> knockout.Survival:
    """P(alive) as it could have been estimated at `md`'s deadline."""
    results = sf.prior_results + strength.results_from_payload(
        sf.payload, sf.season, before_matchday=md)
    elo = strength.fit_elo(sorted(results, key=lambda r: r.date), sf.pots)

    def rating(t: int) -> float:
        return strength.rating_or_prior(elo, t, sf.pots.get(t, 3))

    return knockout.simulate(teams, visible_fixtures(sf.fixtures, md), rating,
                             n_sims=n_sims, seed=int(md))


def _next_rebuild(md: int) -> int:
    later = [u for u in sorted(knockout.UNLIMITED_MATCHDAYS) if u > md]
    return later[0] if later else knockout.FINAL_MD + 1


def _knockout_scores(policy: Policy, sf: SeasonFixtures | None, md: int,
                     view: pd.DataFrame, score: pd.Series
                     ) -> tuple[pd.Series, pd.Series]:
    """(score for this matchday's XI, score for transfers) under a policy."""
    if sf is None or not (policy.playing or policy.alive or policy.rotation):
        return score, score
    vis = visible_fixtures(sf.fixtures, md)
    team = view.team_id
    if policy.rotation and md in knockout.SECOND_LEGS:
        lead = {t: knockout.first_leg_margin(vis, int(t), md)
                for t in team.unique()}
        f = team.map(lambda t: knockout.ROTATION_FACTOR
                     if (lead.get(t) or 0) >= knockout.ROTATION_LEAD else 1.0)
        score = score * f
    if policy.playing:
        playing = {t for f in vis if f.matchday == md
                   for t in (f.home_id, f.away_id)}
        score = score * team.isin(playing).astype(float)
    if not policy.alive:
        return score, score
    surv = survival_at(sf, md, sorted({int(t) for t in team.unique()}))
    weight = pd.Series(0.0, index=view.index)
    for k, m in enumerate(range(md, _next_rebuild(md))):
        p = team.map(lambda t, m=m: surv.p_plays(int(t), m))
        weight += (LOOKAHEAD_DECAY ** k) * p
    return score, form_projection(view) * weight


def replay(df: pd.DataFrame, policy: str | Policy = "planner",
           seed: int = 0, fixtures: SeasonFixtures | None = None
           ) -> SeasonResult:
    """Run one season under one policy and score it against real results."""
    pol = (policy if isinstance(policy, Policy)
           else POLICIES.get(policy, Policy(policy)))
    rng = random.Random(seed)
    mds = sorted(int(m) for m in df.matchday.unique())
    res = SeasonResult(season=str(df.season.iloc[0]), policy=pol.name)
    actual = {(int(r.player_id), int(r.matchday)): int(r.md_points)
              for r in df.itertuples()}

    squad: set[int] = set()
    banked = FREE_PER_MATCHDAY

    for md in mds:
        view = _legal_view(df, md)
        if view.empty:
            continue
        score = form_projection(view)
        if pol.name == "random":
            score = pd.Series(rng.random(), index=score.index).map(
                lambda _: rng.random())
        score, pick = _knockout_scores(pol, fixtures, md, view, score)
        if pol.ko_rules:
            budget = knockout.budget_for(md, BUDGET, 1)
            per_club = knockout.max_per_club(md)
        else:
            budget, per_club = BUDGET, MAX_PER_CLUB

        if not squad:
            squad = _greedy_squad(view, pick, budget, per_club)
        elif pol.name != "hold" and md not in UNLIMITED_MATCHDAYS:
            if pol.ko_rules:
                free = (banked if knockout.carries(md)
                        else knockout.free_transfers(md) or FREE_PER_MATCHDAY)
            else:
                free = min(banked, FREE_PER_MATCHDAY + MAX_CARRY)
            done = _transfer(squad, view, pick, free, budget,
                             per_club if pol.ko_rules else None)
            res.transfers += done
            if pol.ko_rules:
                banked = FREE_PER_MATCHDAY + min(MAX_CARRY, max(0, free - done))
            else:
                # the original bookkeeping, kept so "planner" reproduces the
                # published baseline: it subtracts the SEASON's transfers
                banked = FREE_PER_MATCHDAY + max(
                    0, min(MAX_CARRY, free - res.transfers))
        elif md in UNLIMITED_MATCHDAYS and pol.name != "hold":
            squad = _greedy_squad(view, pick, budget, per_club)
            banked = FREE_PER_MATCHDAY

        xi, captain = _best_xi(squad, score, view)
        got = sum(actual.get((p, md), 0) for p in xi)
        cap_pts = actual.get((captain, md), 0) if captain else 0
        res.total += got + cap_pts
        res.captain_points += cap_pts
        res.per_matchday.append(got + cap_pts)
    return res


def _transfer(squad: set[int], view: pd.DataFrame, score: pd.Series,
              free: int, budget: float, per_club: int | None) -> int:
    """Swap the `free` lowest-scored owners for better affordable ones.

    Mutates `squad`; returns the number of transfers made. `per_club` None is
    the original behaviour, which never checked the club limit.
    """
    owned = [p for p in squad if p in score.index]
    if not owned or not free:
        return 0
    done = 0
    for out in sorted(owned, key=lambda p: score[p])[:free]:
        pos = view.loc[out, "pos"]
        spare = budget - sum(
            view.loc[p, "value"] for p in squad if p in view.index
        ) + view.loc[out, "value"]
        cands = view[(view.pos == pos) & (~view.index.isin(squad))
                     & (view.value <= spare)]
        if per_club is not None:
            count = view.loc[[p for p in squad if p in view.index and p != out],
                             "team_id"].value_counts()
            full = set(count[count >= per_club].index)
            cands = cands[~cands.team_id.isin(full)]
        if cands.empty:
            continue
        best = score[cands.index].idxmax()
        if score[best] > score[out]:
            squad.discard(out)
            squad.add(best)
            done += 1
    return done


def compare(csv_path: Path, season: str,
            policies=("hold", "planner", "random"),
            fixtures: SeasonFixtures | None = None) -> list[SeasonResult]:
    df = load_season(csv_path, season)
    return [replay(df, p, fixtures=fixtures) for p in policies]


def demo():
    """Self-check: leak discipline holds and the harness discriminates."""
    rows = []
    for md in range(1, 18):
        for pid in range(1, 80):
            pos = ["GK", "DEF", "MID", "FWD"][pid % 4]
            # good players score more; the signal is learnable from PAST rounds
            base = 6 if pid < 20 else 1
            rows.append({"season": "T", "tour": 1, "matchday": md, "player_id": pid,
                             "name": f"P{pid}", "team_id": pid % 12, "team": "t",
                             "pos": pos, "value": 4.0 + (pid % 8) * 0.5,
                             "md_points": base + (pid + md) % 3,
                             "selected_pct": 1, "status": "",
                             "season_total_points": 999,   # the answer: must leak nowhere
                             "season_minutes": 0, "season_recoveries": 0,
                             "season_goals": 0, "season_assists": 0,
                             "season_clean_sheets": 0, "season_saves": 0,
                             "season_motm": 0})
    df = pd.DataFrame(rows)

    # the legal view must expose no season-final column, at any matchday
    for md in (1, 9, 17):
        v = _legal_view(df, md)
        assert not set(v.columns) & set(FORBIDDEN), set(v.columns) & set(FORBIDDEN)
        # and must contain no information from matchday md or later
        assert "md_points" not in v.columns

    # at MD1 there is no past, so form counts must all be zero
    assert (_legal_view(df, 1)["count"] == 0).all(), "MD1 saw in-season data"

    planner = replay(df, "planner")
    hold = replay(df, "hold")
    rand = replay(df, "random")
    assert planner.total > 0 and len(planner.per_matchday) == 17
    # a policy that reads form must beat a random one on a learnable signal
    assert planner.total > rand.total, (planner.total, rand.total)
    assert planner.transfers > 0 and hold.transfers == 0

    # Knockouts: clubs 0-5 reach MD9, clubs 0-1 MD13; everyone else is out
    # and scores nothing. Pairings must stay hidden until their round is
    # drawn, results until they are played -- and a policy that knows who is
    # still playing must not do worse than one that does not.
    fx = []
    for md in range(1, 18):
        alive = 12 if md <= 8 else 6 if md <= 12 else 2
        fx += [knockout.Fixture(md, t, t + 1, 1, 0) for t in range(0, alive, 2)]
    ko = df.copy()
    out_at = ko.team_id.map(lambda t: 9 if t >= 6 else 13 if t >= 2 else 99)
    ko.loc[ko.matchday >= out_at, "md_points"] = 0
    sf = SeasonFixtures(fx, {"data": {"value": []}}, "T", {}, [])
    v8, v9 = visible_fixtures(fx, 8), visible_fixtures(fx, 9)
    assert not any(knockout.is_knockout(f.matchday) for f in v8)
    assert {f.matchday for f in v9 if f.matchday > 8} == {9, 10}
    assert not any(f.played for f in v9 if f.matchday >= 9)
    rules = replay(ko, "rules", fixtures=sf)
    playing = replay(ko, "playing", fixtures=sf)
    alive_r = replay(ko, "alive", fixtures=sf)
    assert playing.total >= rules.total, (playing.total, rules.total)
    assert sum(playing.per_matchday[8:]) > sum(rules.per_matchday[8:])
    assert alive_r.total > 0

    print(f"backtest.py self-check OK — no leak; planner {planner.total} > "
          f"random {rand.total}, hold {hold.total}; knockouts: playing "
          f"{playing.total} >= rules {rules.total}")


if __name__ == "__main__":
    demo()
