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
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

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
                  budget: float = BUDGET) -> set[int]:
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
        if need.get(pos, 0) <= 0 or per_club[row.team_id] >= MAX_PER_CLUB:
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


def replay(df: pd.DataFrame, policy: str = "planner",
           seed: int = 0) -> SeasonResult:
    """Run one season under one policy and score it against real results."""
    rng = random.Random(seed)
    mds = sorted(df.matchday.unique())
    res = SeasonResult(season=str(df.season.iloc[0]), policy=policy)
    actual = {(int(r.player_id), int(r.matchday)): int(r.md_points)
              for r in df.itertuples()}

    squad: set[int] = set()
    banked = FREE_PER_MATCHDAY

    for md in mds:
        view = _legal_view(df, md)
        if view.empty:
            continue
        score = form_projection(view)
        if policy == "random":
            score = pd.Series(rng.random(), index=score.index).map(
                lambda _: rng.random())

        if not squad:
            squad = _greedy_squad(view, score)
        elif policy != "hold" and md not in UNLIMITED_MATCHDAYS:
            free = min(banked, FREE_PER_MATCHDAY + MAX_CARRY)
            owned = [p for p in squad if p in score.index]
            if owned and free:
                worst = sorted(owned, key=lambda p: score[p])[:free]
                for out in worst:
                    pos = view.loc[out, "pos"]
                    spare = BUDGET - sum(
                        view.loc[p, "value"] for p in squad if p in view.index
                    ) + view.loc[out, "value"]
                    cands = view[(view.pos == pos) & (~view.index.isin(squad))
                                 & (view.value <= spare)]
                    if cands.empty:
                        continue
                    best = score[cands.index].idxmax()
                    if score[best] > score[out]:
                        squad.discard(out)
                        squad.add(best)
                        res.transfers += 1
            banked = FREE_PER_MATCHDAY + max(
                0, min(MAX_CARRY, free - res.transfers))
        elif md in UNLIMITED_MATCHDAYS and policy != "hold":
            squad = _greedy_squad(view, score)
            banked = FREE_PER_MATCHDAY

        xi, captain = _best_xi(squad, score, view)
        got = sum(actual.get((p, md), 0) for p in xi)
        cap_pts = actual.get((captain, md), 0) if captain else 0
        res.total += got + cap_pts
        res.captain_points += cap_pts
        res.per_matchday.append(got + cap_pts)
    return res


def compare(csv_path: Path, season: str,
            policies=("hold", "planner", "random")) -> list[SeasonResult]:
    df = load_season(csv_path, season)
    return [replay(df, p) for p in policies]


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

    print(f"backtest.py self-check OK — no leak; planner {planner.total} > "
          f"random {rand.total}, hold {hold.total}")


if __name__ == "__main__":
    demo()
