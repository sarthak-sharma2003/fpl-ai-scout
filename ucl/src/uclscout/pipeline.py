"""End-to-end: live feeds -> Elo -> projections -> squad -> multi-matchday plan."""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import domestic, feeds, plan, project, strength

ARCHIVED_TOURS = [30, 40, 50, 60, 70, 80]
PREVIOUS_TOUR = 80          # 2025-26, for detecting who changed clubs
# Elo-difference -> multiplier on a player's points. Bounded because a rating
# gap should tilt a projection, never dominate it.
ELO_COEF = 0.00055
ADJ_FLOOR, ADJ_CEIL = 0.70, 1.35
# DOMESTIC ELO IS OFF, and this is a deliberate negative result.
#
# football-data.co.uk gives 15,644 free domestic matches covering 32 of the 36
# clubs -- 18x the 878 UCL matches. Fitting them in makes the ratings WORSE:
#
#   UCL only     ARS 1815  PSG 1803  BAY 1782  RMA 1680  BAR 1680  LIV 1650
#   + domestic   BAY 1786  BOD 1759  ARS 1733  BAR 1709  AEK 1709  SPO 1702
#
# Bodo/Glimt second in Europe and AEK Athens fifth. Elo is league-relative: a
# club dominating the Eliteserien looks identical to one dominating the
# Bundesliga, because nothing anchors the two scales together. The UCL matches
# ARE that anchor, and 878 of them cannot hold against 15,644.
#
# Down-weighting does not fix it -- it shrinks the distortion but not its
# direction; Bodo/Glimt still placed top-5 at every weight from 0.05 to 1.0.
# The real fix is a per-league strength OFFSET (UEFA country coefficients would
# supply one) applied before fitting, so each league starts on a calibrated
# scale. Until then this stays off and the ratings come from continental
# matches only, where every result is inherently cross-league.
#
# Set to a positive value only alongside that offset, and judge it on the
# backtest rather than on the rating table.
DOMESTIC_WEIGHT = 0.0


@dataclass
class Context:
    tour: int
    constraints: feeds.Constraints
    teams: list[feeds.Team]
    matchdays: list[feeds.Matchday]
    players: list[feeds.Player]
    elo: dict[int, float]
    moved_clubs: set[int]      # players whose prior stats were at another club

    @property
    def team_by_id(self) -> dict[int, feeds.Team]:
        return {t.id: t for t in self.teams}

    def rating(self, team_id: int) -> float:
        t = self.team_by_id.get(team_id)
        return strength.rating_or_prior(self.elo, team_id, t.pot if t else 3)

    def strength_adj(self, own: int, opp: int, home: bool) -> float:
        diff = self.rating(own) - self.rating(opp)
        diff += strength.HOME_ADV if home else -strength.HOME_ADV
        return max(ADJ_FLOOR, min(ADJ_CEIL, 1.0 + ELO_COEF * diff))

    def opponents_for(self, md: int) -> dict[int, tuple[int, bool, str]]:
        """team_id -> (opponent_id, is_home, kickoff day) for one matchday."""
        out: dict[int, tuple[int, bool, str]] = {}
        for m in next((x.matches for x in self.matchdays if x.id == md), []):
            day = m.kickoff.date().isoformat()
            out[m.home_id] = (m.away_id, True, day)
            out[m.away_id] = (m.home_id, False, day)
        return out


def detect_transfers(current: list[feeds.Player],
                     previous_tour: int = PREVIOUS_TOUR) -> set[int]:
    """Players at a different club than last season.

    UEFA publishes a player's CURRENT club alongside his PRIOR-season stats and
    gives no club-of-record field, so prior minutes silently read as if earned
    at the new club. Diffing last season's feed is the only way to see it.
    Anything we cannot check is treated as moved -- the safe direction, since
    the consequence is only that he is barred from captaincy.
    """
    try:
        before = {p.id: p.team_id for p in feeds.players(previous_tour, 1)}
    except Exception:                                        # noqa: BLE001
        return set()
    return {p.id for p in current
            if p.minutes > 0 and before.get(p.id, p.team_id) != p.team_id}


def fit_ratings(teams: list[feeds.Team], tour: int,
                use_domestic: bool = False) -> dict[int, float]:
    """Elo over UCL results plus domestic league results.

    Continental matches only by default -- see DOMESTIC_WEIGHT for why adding
    15,644 domestic matches made the ratings worse rather than better.
    """
    pots = {t.id: t.pot for t in teams}
    results = strength.historical_results(ARCHIVED_TOURS + [tour])
    if use_domestic and DOMESTIC_WEIGHT > 0:
        try:
            results += [
                replace(r, weight=DOMESTIC_WEIGHT)
                for r in domestic.domestic_results(teams)
            ]
        except Exception:                                    # noqa: BLE001
            pass          # domestic source down: UCL-only Elo still works
    results.sort(key=lambda r: r.date)
    return strength.fit_elo(results, pots)


def load(tour: int = feeds.CURRENT_TOUR, *, fit_strength: bool = True) -> Context:
    c = feeds.constraints(tour)
    teams = feeds.teams(tour)
    mds = feeds.matchdays(tour)
    pl = feeds.players(tour, c.matchday)
    elo = fit_ratings(teams, tour) if fit_strength else {}
    return Context(tour, c, teams, mds, pl, elo, detect_transfers(pl))


def project_matchday(ctx: Context, md: int, **kw) -> list[project.Projection]:
    kw.setdefault("moved_clubs", ctx.moved_clubs)
    return project.project(
        ctx.players, ctx.teams, ctx.opponents_for(md),
        strength_adj=ctx.strength_adj, **kw,
    )


def horizon(ctx: Context, upto: int | None = None, **kw
            ) -> dict[int, list[project.Projection]]:
    """Projections for every remaining matchday with published fixtures."""
    start = ctx.constraints.matchday
    end = upto or max((m.id for m in ctx.matchdays), default=start)
    out = {}
    for md in range(start, end + 1):
        opp = ctx.opponents_for(md)
        if not opp:
            continue                     # knockout fixtures not drawn yet
        out[md] = project_matchday(ctx, md, **kw)
    return out


def build_plan(ctx: Context, *, current_squad: set[int] | None = None,
               time_limit: int = 120, **kw) -> plan.Plan:
    h = horizon(ctx, **kw)
    p = plan.plan_horizon(
        h,
        current_squad=current_squad,
        budget=ctx.constraints.budget,
        max_per_club=ctx.constraints.max_per_club,
        time_limit=time_limit,
    )
    squads: dict[int, set[int]] = {}
    owned = set(current_squad or (
        {x.player.id for x in p.opening_squad.picks} if p.opening_squad else set()))
    for a in p.actions:
        owned = (owned | {b.player.id for b in a.buys}) - {
            s.player.id for s in a.sells}
        squads[a.matchday] = set(owned)
    try:
        p.limitless = plan.limitless_matchday(
            h, squads, budget=ctx.constraints.budget,
            max_per_club=ctx.constraints.max_per_club)
    except ValueError:
        p.limitless = None
    return p
