"""End-to-end: live feeds -> Elo -> projections -> squad -> multi-matchday plan."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from . import domestic, feeds, knockout, lineups, plan, project, strength

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
    # P(club still in, and playing) for every matchday to the final. None when
    # it could not be estimated: the horizon then stops at the last published
    # fixtures, exactly as it did before the knockouts were modelled.
    survival: knockout.Survival | None = None

    @property
    def team_by_id(self) -> dict[int, feeds.Team]:
        return {t.id: t for t in self.teams}

    @property
    def eliminated(self) -> set[int]:
        """Clubs out of the competition, by either signal the feeds carry.

        `Team.isEliminated`, or most of a club's players at status "E" (how
        the completed 2025-26 player feed marks every eliminated club).
        """
        out = {t.id for t in self.teams if t.eliminated}
        tally: dict[int, list[int]] = {}
        for p in self.players:
            row = tally.setdefault(p.team_id, [0, 0])
            row[0] += p.status == feeds.ELIMINATED
            row[1] += 1
        out |= {t for t, (e, n) in tally.items() if n >= 5 and 2 * e >= n}
        return out

    @property
    def fixtures(self) -> list[knockout.Fixture]:
        return knockout.fixtures_from(self.matchdays)

    def rating(self, team_id: int) -> float:
        t = self.team_by_id.get(team_id)
        return strength.rating_or_prior(self.elo, team_id, t.pot if t else 3)

    def strength_adj(self, own: int, opp: int, home: bool) -> float:
        return self.adj_vs_rating(own, self.rating(opp), home)

    def adj_vs_rating(self, own: int, opp_rating: float,
                      home: bool | None) -> float:
        """Points multiplier against an opponent of a given rating; `home`
        None is a neutral venue (or an undrawn tie, home in one leg of two)."""
        diff = self.rating(own) - opp_rating
        if home is not None:
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
    ctx.survival = estimate_survival(ctx)
    if predicted_lineups:
        attach_lineups(ctx)
    return ctx


# 2,000 runs put a standard error of at most 1.1 points on any P(alive), and
# take well under a second.
SURVIVAL_SIMS = 2000


def estimate_survival(ctx: Context, n_sims: int = SURVIVAL_SIMS
                      ) -> knockout.Survival | None:
    """P(each club plays each remaining matchday). Never raises: without it
    the plan just stops at the last published fixtures, as it used to."""
    try:
        return knockout.simulate([t.id for t in ctx.teams], ctx.fixtures,
                                 ctx.rating, eliminated=ctx.eliminated,
                                 n_sims=n_sims)
    except Exception:                                        # noqa: BLE001
        return None


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
    """Every available player's projection for one matchday.

    A knockout matchday whose fixtures are not drawn yet is projected against
    a generic opponent and weighted by P(his club plays it) -- see
    `project_undrawn`. Clubs out of the competition are never projected.
    """
    kw.setdefault("moved_clubs", ctx.moved_clubs)
    kw.setdefault("prior_season", ctx.prior_season)
    opponents = ctx.opponents_for(md)
    if not opponents:
        return project_undrawn(ctx, md, **kw) if knockout.is_knockout(md) else []
    dead = ctx.eliminated
    opponents = {t: o for t, o in opponents.items() if t not in dead}
    # line-ups describe each club's NEXT match: never project them further out
    if md == ctx.lineup_matchday and ctx.lineup_probs:
        kw.setdefault("predicted_lineups", ctx.lineup_probs)
    projs = project.project(
        ctx.players, ctx.teams, opponents,
        strength_adj=ctx.strength_adj, **kw,
    )
    return second_leg_rotation(ctx, md, projs)


def project_undrawn(ctx: Context, md: int, **kw) -> list[project.Projection]:
    """A knockout matchday before its draw: P(plays) x a generic opponent.

    The opponent is the AVERAGE rating a club meets in that round across the
    simulations in which it gets there, at a neutral venue (one leg home, one
    away). Clubs with no chance of playing are left out; the planner treats a
    missing club as a zero for that matchday.
    """
    surv = ctx.survival
    if surv is None:
        return []
    dead = ctx.eliminated
    p_plays = {t.id: surv.p_plays(t.id, md) for t in ctx.teams
               if t.id not in dead}
    p_plays = {t: p for t, p in p_plays.items() if p > 0}
    if not p_plays:
        return []
    opp_rating = {t: surv.opp_rating.get(t, {}).get(md, ctx.rating(t))
                  for t in p_plays}

    def adj(own: int, _opp: int, _home: bool) -> float:
        return ctx.adj_vs_rating(own, opp_rating[own], None)

    kw.pop("predicted_lineups", None)        # line-ups are for the next match
    projs = project.project(
        ctx.players, ctx.teams, {t: (0, None, None) for t in p_plays},
        strength_adj=adj, **kw)
    return sorted((replace(p, points=p.points * p_plays[p.player.team_id],
                           opponent_id=None) for p in projs),
                  key=lambda p: -p.points)


def second_leg_rotation(ctx: Context, md: int,
                        projs: list[project.Projection]
                        ) -> list[project.Projection]:
    """Discount a club that leads its tie by `knockout.ROTATION_LEAD`+.

    Only on a second leg whose first leg has a result in the feed.
    """
    if md not in knockout.SECOND_LEGS:
        return projs
    fixtures = ctx.fixtures
    margin: dict[int, int | None] = {}
    out = []
    for p in projs:
        t = p.player.team_id
        if t not in margin:
            margin[t] = knockout.first_leg_margin(fixtures, t, md)
        lead = margin[t]
        if lead is None or lead < knockout.ROTATION_LEAD:
            out.append(p)
            continue
        f = knockout.ROTATION_FACTOR
        m = p.minutes
        mins = replace(m, expected=m.expected * f,
                       p_start=None if m.p_start is None else m.p_start * f,
                       note=f"{m.note}; {lead}-goal first-leg lead: rotation risk")
        out.append(replace(p, minutes=mins, points=p.points * f))
    return sorted(out, key=lambda p: -p.points)


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
    """Projections for every remaining matchday to the final.

    Published fixtures are projected as they are; knockout matchdays not
    drawn yet are projected P(alive)-weighted against a generic opponent
    (`project_undrawn`). Without a survival estimate the horizon stops at the
    last published fixtures, as it always did.
    """
    start = next_open_matchday(ctx)
    ends = [m.id for m in ctx.matchdays if m.matches]
    if ctx.survival is not None:
        ends.append(min(knockout.FINAL_MD, ctx.constraints.last_matchday))
    end = upto or max(ends, default=start)
    out = {}
    for md in range(start, end + 1):
        projs = project_matchday(ctx, md, **kw)
        if projs:
            out[md] = projs
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


def segments(mds: list[int]) -> list[list[int]]:
    """Split a horizon at the free rebuilds (MD9, MD11).

    Everyone gets unlimited transfers there, and prices are held flat inside
    the plan, so no squad choice before a rebuild constrains one after it:
    each stretch can be solved on its own. Only the wildcard (one a season)
    couples them, and `build_plan` prices that coupling explicitly. Three
    7-matchday solves instead of one 16-matchday MILP is what keeps the whole
    season tractable inside a publish.
    """
    out: list[list[int]] = []
    for md in sorted(mds):
        if not out or md in plan.UNLIMITED_MATCHDAYS:
            out.append([md])
        else:
            out[-1].append(md)
    return out


def rules_for(ctx: Context, mds: list[int], funds: float, funds_md: int
              ) -> tuple[dict[int, float], dict[int, int]]:
    """Budget and max-per-club on every matchday of a plan (docs/GAPS.md #7).

    The feed's own max-per-club wins for the matchday it describes; the round
    table covers the rest.
    """
    budgets = {md: knockout.budget_for(md, funds, funds_md) for md in mds}
    clubs = {md: knockout.max_per_club(md) for md in mds}
    if ctx.constraints.matchday in clubs:
        clubs[ctx.constraints.matchday] = ctx.constraints.max_per_club
    return budgets, clubs


# The knockout stretches get this share of the solver time each (two solves
# per stretch: with and without the wildcard).
TAIL_TIME_SHARE = 0.25


def _plan_tail(h, tail: list[list[int]], budgets, clubs, *,
               allow_wildcard: bool, decay_from: int, time_limit: int):
    """Plan each post-rebuild stretch fresh, with and without the wildcard.

    Returns [(plan without, plan with or None, wildcard gain)] -- the gain is
    the solver objective's difference, so it is already net of the residual
    `plan.WILDCARD_HOLD_VALUE` charged inside the "with" solve.
    """
    out = []
    t = max(10, int(time_limit * TAIL_TIME_SHARE))
    for seg in tail:
        hh = {md: h[md] for md in seg}
        kw = {"budget": budgets, "max_per_club": clubs, "decay_from": decay_from,
              "time_limit": t}
        base = plan.plan_horizon(hh, allow_wildcard=False, **kw)
        with_wc = (plan.plan_horizon(hh, allow_wildcard=True, **kw)
                   if allow_wildcard and len(seg) > 1 else None)
        gain = (with_wc.objective - base.objective
                if with_wc is not None
                and any(a.chip == "wildcard" for a in with_wc.actions) else 0.0)
        out.append((base, with_wc, gain))
    return out


def _join(head: plan.Plan, tails: list[plan.Plan]) -> None:
    """Append knockout stretches to the head plan; a stretch's opening build
    becomes an unlimited rebuild of the squad held the matchday before."""
    for t in tails:
        prev = head.actions[-1]
        first = t.actions[0]
        before = {p.player.id for p in prev.squad}
        after = {p.player.id for p in first.squad}
        first.buys = plan.by_position(
            [p for p in first.squad if p.player.id not in before])
        first.sells = plan.by_position(
            [p for p in prev.squad if p.player.id not in after])
        first.unlimited = True
        first.free_available = plan.SQUAD_SIZE
        head.actions += t.actions
        head.total_points += t.total_points
        head.objective += t.objective


def build_plan(ctx: Context, *, current_squad: set[int] | None = None,
               bank: float = 0.0, wildcard_used: bool = False,
               limitless_used: bool = False, free_transfers: int = 2,
               time_limit: int = 120, **kw) -> plan.Plan:
    h = horizon(ctx, **kw)
    if not h:
        raise RuntimeError("nothing to plan: no matchday ahead is projected")
    if current_squad:
        _keep_held(ctx, h, current_squad)
    funds = (squad_budget(ctx, current_squad, bank) if current_squad
             else ctx.constraints.budget)
    first_md = min(h)
    budgets, clubs = rules_for(ctx, sorted(h), funds, first_md)
    segs = segments(sorted(h))
    head_mds, tail = segs[0], segs[1:]

    # The knockout stretches first, so the league plan knows what holding the
    # wildcard is worth: spending it now must beat its best knockout use, and
    # never clears a lower bar than the old fixed WILDCARD_HOLD_VALUE.
    tail_plans, tail_note = [], ""
    hold = plan.WILDCARD_HOLD_VALUE
    if tail:
        try:
            tail_plans = _plan_tail(h, tail, budgets, clubs,
                                    allow_wildcard=not wildcard_used,
                                    decay_from=first_md, time_limit=time_limit)
            hold = max(hold, plan.WILDCARD_HOLD_VALUE
                       + max(g for *_, g in tail_plans))
        except Exception as e:                               # noqa: BLE001
            tail_plans = []
            tail_note = f"knockout outlook unavailable ({type(e).__name__})"

    # A publish that dies leaves the site on stale advice right before a
    # deadline, which is worse than slightly-relaxed advice. Each fallback is
    # recorded on the plan so the page and the alert can say so.
    attempts = [
        ("", {}),
        ("minutes gate dropped", {"min_start": 0.0}),
        ("planned this matchday only", {"min_start": 0.0,
                                        "first_only": True}),
    ]
    head = {md: h[md] for md in head_mds}
    last_err: Exception | None = None
    for note, kw2 in attempts:
        only_first = kw2.pop("first_only", False)
        hh = {min(head): head[min(head)]} if only_first else head
        head_kw = dict(
            current_squad=current_squad,
            budget=budgets,
            max_per_club=clubs,
            allow_wildcard=not wildcard_used,
            initial_free=free_transfers if current_squad else plan.FREE_PER_MATCHDAY,
            time_limit=time_limit,
            **kw2,
        )
        try:
            p = plan.plan_horizon(hh, wildcard_hold=hold, **head_kw)
            p.note = note
            break
        except RuntimeError as e:
            last_err = e
    else:
        raise RuntimeError(f"no feasible plan after fallbacks: {last_err}")
    p.chip_check["wildcard"] = wildcard_check(
        p, hh, head_kw, hold=hold, used=wildcard_used,
        fresh=not current_squad, only_first=only_first)

    if tail_plans and not only_first:
        wc_spent = wildcard_used or any(a.chip == "wildcard" for a in p.actions)
        best = max(range(len(tail_plans)), key=lambda k: tail_plans[k][2])
        chosen = [w if (not wc_spent and k == best and g > 0 and w) else b
                  for k, (b, w, g) in enumerate(tail_plans)]
        _join(p, chosen)
    p.note = "; ".join(filter(None, [p.note, tail_note]))
    published = {m.id for m in ctx.matchdays if m.matches}
    for a in p.actions:
        a.generic = a.matchday not in published

    p.budget = funds
    squads: dict[int, set[int]] = {}
    owned = set(current_squad or (
        {x.player.id for x in p.opening_squad.picks} if p.opening_squad else set()))
    for a in p.actions:
        if a.squad:
            owned = {x.player.id for x in a.squad}
        else:
            owned = (owned | {b.player.id for b in a.buys}) - {
                s.player.id for s in a.sells}
        squads[a.matchday] = set(owned)
    p.limitless = None
    bar = plan.LIMITLESS_HOLD_VALUE
    if limitless_used:
        p.chip_check["limitless"] = {"advice": "used", "bar": bar}
        return p
    planned = {a.matchday for a in p.actions}
    gains = plan.limitless_gains(
        {md: v for md, v in h.items() if md in planned}, squads,
        budget=budgets, max_per_club=clubs)
    p.limitless = plan.best_limitless(gains, decay_from=first_md, min_gain=bar)
    top = plan.best_limitless(gains, decay_from=first_md,
                              min_gain=float("-inf"))
    if p.limitless and p.limitless[0] == first_md:
        p.limitless_squad = gains[first_md][1]
    p.chip_check["limitless"] = {
        "advice": ("hold" if p.limitless is None
                   else "play" if p.limitless[0] == first_md else "planned"),
        "matchday": (p.limitless or top or (None, None))[0],
        "gain": _r1((p.limitless or top or (None, None))[1]),
        "thisMatchday": _r1(gains[first_md][0]) if first_md in gains else None,
        "bar": bar,
        "checked": sorted(gains),
    }
    return p


def _r1(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


def wildcard_check(p: plan.Plan, hh: dict, head_kw: dict, *, hold: float,
                   used: bool, fresh: bool, only_first: bool) -> dict:
    """The wildcard's verdict for this run, published whatever it says.

    The plan spends the wildcard only where it beats holding it by `hold`
    (WILDCARD_HOLD_VALUE, raised to its best knockout use). When it does not,
    a second solve with no hold value says what the best use inside the
    planned stretch WOULD gain -- the number the manager compares to the bar.
    """
    out: dict = {"bar": round(hold, 1)}
    if used:
        return {**out, "advice": "used"}
    if fresh:
        return {**out, "advice": "hold",
                "reason": "every transfer is already free on this matchday"}
    at = next((a.matchday for a in p.actions if a.chip == "wildcard"), None)
    if at is not None:
        return {**out, "advice": "play" if at == min(hh) else "planned",
                "matchday": at}
    if only_first or len(hh) < 2:
        return {**out, "advice": "hold"}
    try:
        probe = plan.plan_horizon(hh, wildcard_hold=0.0, **head_kw)
    except RuntimeError:
        return {**out, "advice": "hold"}
    best = next((a.matchday for a in probe.actions if a.chip == "wildcard"),
                None)
    gain = max(0.0, probe.objective - p.objective) if best else 0.0
    return {**out, "advice": "hold", "matchday": best, "gain": _r1(gain)}
