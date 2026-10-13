"""End-to-end: live feeds -> Elo -> projections -> squad -> multi-matchday plan."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from . import domestic, feeds, lineups, plan, project, strength

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
    # last season's UCL totals for players still at the same club (empty at
    # MD1, when the live feed itself still carries last season's numbers)
    prior_season: dict[int, project.PriorSeason] = field(default_factory=dict)
    # predicted-line-up P(start), valid for `lineup_matchday` ONLY: the sources
    # describe each club's next match, never a later one
    lineup_probs: dict[int, float] = field(default_factory=dict)
    lineup_matchday: int | None = None
    lineup_report: lineups.LineupReport | None = None

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


def prior_season_stats(current: list[feeds.Player], previous: list[feeds.Player],
                       moved: set[int]) -> dict[int, project.PriorSeason]:
    """Last season's UCL totals for everyone still at the same club."""
    peak: dict[int, float] = {}
    for p in previous:
        peak[p.team_id] = max(peak.get(p.team_id, 0.0), float(p.minutes))
    prev = {p.id: p for p in previous}
    out = {}
    for p in current:
        q = prev.get(p.id)
        if q is None or p.id in moved or q.team_id != p.team_id or q.minutes <= 0:
            continue
        out[p.id] = project.PriorSeason(q.total_points, q.minutes,
                                        peak.get(q.team_id, 0.0))
    return out


def detect_transfers(current: list[feeds.Player],
                     previous_tour: int = PREVIOUS_TOUR,
                     previous: list[feeds.Player] | None = None) -> set[int]:
    """Players at a different club than last season.

    UEFA publishes a player's CURRENT club alongside his PRIOR-season stats and
    gives no club-of-record field, so prior minutes silently read as if earned
    at the new club. Diffing last season's feed is the only way to see it.
    Anything we cannot check is treated as moved -- the safe direction, since
    the consequence is only that he is barred from captaincy.
    """
    try:
        if previous is None:
            previous = feeds.players(previous_tour, 1)
        before = {p.id: p.team_id for p in previous}
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


def load(tour: int = feeds.CURRENT_TOUR, *, fit_strength: bool = True,
         predicted_lineups: bool = True) -> Context:
    c = feeds.constraints(tour)
    teams = feeds.teams(tour)
    mds = feeds.matchdays(tour)
    pl = feeds.players(tour, c.matchday)
    elo = fit_ratings(teams, tour) if fit_strength else {}
    try:
        # a completed season's files all carry its season-final totals
        previous = feeds.players(PREVIOUS_TOUR, 1)
    except Exception:                                        # noqa: BLE001
        previous = []
    moved = detect_transfers(pl, previous=previous) if previous else set()
    prior = (prior_season_stats(pl, previous, moved)
             if previous and c.matchday > 1 else {})
    ctx = Context(tour, c, teams, mds, pl, elo, moved, prior)
    if predicted_lineups:
        attach_lineups(ctx)
    return ctx


def attach_lineups(ctx: Context, *, now: datetime | None = None,
                   data: dict | None = None) -> None:
    """Predicted-line-up P(start) for the next open matchday, where trustworthy.

    Only clubs kicking off within `lineups.FRESH_HOURS` are asked for: further
    out, a club's "next match" may be a league game in between. Never raises --
    no line-ups just means the observed-minutes projection, as before.
    """
    now = now or datetime.now(UTC)
    md = next_open_matchday(ctx, now)
    this = next((m for m in ctx.matchdays if m.id == md), None)
    fresh = lineups.fresh_clubs(this, now)
    if not fresh:
        ctx.lineup_report = lineups.LineupReport(
            note=f"MD{md}: no club kicks off within "
                 f"{lineups.FRESH_HOURS:.0f}h; not fetched")
        return
    try:
        rep = lineups.start_probabilities(ctx.players, ctx.teams,
                                          clubs=fresh, data=data)
    except Exception as e:                                   # noqa: BLE001
        ctx.lineup_report = lineups.LineupReport(
            note=f"line-ups failed: {type(e).__name__}: {e}"[:200])
        return
    rep.note = (f"MD{md}: {len(fresh)} clubs kick off within "
                f"{lineups.FRESH_HOURS:.0f}h")
    ctx.lineup_probs, ctx.lineup_matchday, ctx.lineup_report = rep.probs, md, rep


def project_matchday(ctx: Context, md: int, **kw) -> list[project.Projection]:
    kw.setdefault("moved_clubs", ctx.moved_clubs)
    kw.setdefault("prior_season", ctx.prior_season)
    # line-ups describe each club's NEXT match: never project them further out
    if md == ctx.lineup_matchday and ctx.lineup_probs:
        kw.setdefault("predicted_lineups", ctx.lineup_probs)
    return project.project(
        ctx.players, ctx.teams, ctx.opponents_for(md),
        strength_adj=ctx.strength_adj, **kw,
    )


def next_open_matchday(ctx: Context, now: datetime | None = None) -> int:
    """The first matchday whose deadline is still ahead.

    UEFA keeps `matchdayId` on a matchday until it has finished, so between the
    deadline and the last final whistle the feed's "current" matchday is one
    you can no longer change. Planning it again would re-advise moves that are
    already locked in.
    """
    now = now or datetime.now(UTC)
    md = ctx.constraints.matchday
    by_id = {m.id: m for m in ctx.matchdays}
    while md in by_id and by_id[md].deadline <= now:
        md += 1
    return md


def horizon(ctx: Context, upto: int | None = None, **kw
            ) -> dict[int, list[project.Projection]]:
    """Projections for every remaining matchday with published fixtures."""
    start = next_open_matchday(ctx)
    end = upto or max((m.id for m in ctx.matchdays), default=start)
    out = {}
    for md in range(start, end + 1):
        opp = ctx.opponents_for(md)
        if not opp:
            continue                     # knockout fixtures not drawn yet
        out[md] = project_matchday(ctx, md, **kw)
    return out


def squad_budget(ctx: Context, held: set[int], bank: float) -> float:
    """What a held squad can spend: its 15 at today's prices plus the bank.

    UCL prices move between matchdays, so the nominal EUR100m is wrong for a
    squad bought earlier -- risers have grown it, fallers shrunk it.
    """
    value = {p.id: p.value for p in ctx.players}
    if not held or any(i not in value for i in held):
        return ctx.constraints.budget
    return round(sum(value[i] for i in held) + bank, 1)


def _keep_held(ctx: Context, h: dict[int, list[project.Projection]],
               held: set[int]) -> None:
    """Give owned players a zero projection where they have none.

    `project` skips unavailable players and clubs without a fixture, and the
    planner only models players projected in every matchday -- so an injured
    owned player silently fell out of the squad, and the plan "bought" his
    replacement without selling him. He must stay: benchable, sellable, worth 0.
    """
    by_id = {p.id: p for p in ctx.players}
    for projs in h.values():
        have = {x.player.id for x in projs}
        for pid in held - have:
            if pid in by_id:
                projs.append(project.Projection(
                    by_id[pid],
                    project.Minutes(0.0, project.MinutesSource.OBSERVED_UCL,
                                    "unavailable or no fixture"),
                    0.0, 1.0, 0.0, None, None, None))


def build_plan(ctx: Context, *, current_squad: set[int] | None = None,
               bank: float = 0.0, wildcard_used: bool = False,
               limitless_used: bool = False,
               time_limit: int = 120, **kw) -> plan.Plan:
    h = horizon(ctx, **kw)
    if current_squad:
        _keep_held(ctx, h, current_squad)
    budget = (squad_budget(ctx, current_squad, bank) if current_squad
              else ctx.constraints.budget)
    # A publish that dies leaves the site on stale advice right before a
    # deadline, which is worse than slightly-relaxed advice. Each fallback is
    # recorded on the plan so the page and the alert can say so.
    attempts = [
        ("", {}),
        ("minutes gate dropped", {"min_start": 0.0}),
        ("planned this matchday only", {"min_start": 0.0,
                                        "first_only": True}),
    ]
    last_err: Exception | None = None
    for note, kw2 in attempts:
        hh = {min(h): h[min(h)]} if kw2.pop("first_only", False) and h else h
        try:
            p = plan.plan_horizon(
                hh,
                current_squad=current_squad,
                budget=budget,
                max_per_club=ctx.constraints.max_per_club,
                allow_wildcard=not wildcard_used,
                time_limit=time_limit,
                **kw2,
            )
            p.note = note
            break
        except RuntimeError as e:
            last_err = e
    else:
        raise RuntimeError(f"no feasible plan after fallbacks: {last_err}")
    p.budget = budget
    squads: dict[int, set[int]] = {}
    owned = set(current_squad or (
        {x.player.id for x in p.opening_squad.picks} if p.opening_squad else set()))
    for a in p.actions:
        owned = (owned | {b.player.id for b in a.buys}) - {
            s.player.id for s in a.sells}
        squads[a.matchday] = set(owned)
    if limitless_used:
        p.limitless = None
        return p
    try:
        p.limitless = plan.limitless_matchday(
            h, squads, budget=ctx.constraints.budget,
            max_per_club=ctx.constraints.max_per_club)
    except ValueError:
        p.limitless = None
    return p
