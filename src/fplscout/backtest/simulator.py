"""Season replay simulator — plan §Phase6, the go/no-go gate.

Trains models ONCE on `train_seasons` (never sees the replayed season), then walks
gameweek by gameweek: horizon-aware EV (models/horizon.py — leak-safe multi-step
forecast, frozen player-form + real per-target-gw fixture context) -> optimizer
decision -> score with ACTUAL results (with real auto-subs and captain fallback)
-> update bank/free transfers/squad for the next GW. Prices use vaastav's real
historical `value` column directly rather than modeling price-change dynamics —
plan §Phase6 asked for a simple price model "using vaastav price data"; the actual
historical price at each GW *is* vaastav price data, and is exact where a modeled
approximation would just be a noisier version of the same number.

The horizon-EV computation went through two versions before this one, both real
bugs caught by concrete evidence (see models/horizon.py's docstring and git log
for the full trail, not summarized here to avoid the two docstrings drifting out
of sync): v1 leaked future match outcomes into earlier decisions; v2 fixed the leak
by flattening to a single current-gw projection, which removed fixture-awareness
entirely and caused unrealistic hit-chasing on single-gameweek noise. v3
(models/horizon.py) restores fixture-awareness without the leak.

Known scope limitations, not oversights:

- Only bench_boost and triple_captain timing is planned and simulated
  (`_plan_chips_for_season`). The optimizer's normal weekly transfer logic
  (with hits) is what wildcard would otherwise disable, so squad quality
  doesn't collapse without it — but the chip's OWN uplift (a burst of free
  transfers to fix a squad that's drifted far from optimal) is not modeled or
  scored, and free_hit needs squad-reversion state (the squad must revert to
  its pre-chip form the following GW) that neither remaining chip shares —
  both deliberately deferred rather than rushed and shipped half-tested under
  time pressure. `simulate_season`'s `chips_available` dict still reserves
  budget for both (2 each) so a future pass can add them without touching
  this loop's structure.
- `_plan_chips_for_season` fires each chip on a fixed, staggered gameweek near
  the end of its half-window (bench_boost last, triple_captain second-to-last)
  rather than searching for the best gameweek within the window, for the same
  look-ahead reason models/horizon.py's docstring describes. A genuinely
  walk-forward chip-timing policy (fire when the CURRENT gameweek's own
  leak-safe value clears a training-data threshold, else hold) is real
  follow-up work, not shipped here half-tested under time pressure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import pandas as pd

from fplscout.backtest.autosub import apply_autosubs
from fplscout.decide import chip_planner
from fplscout.decide.optimizer import OptimizerInput, optimize
from fplscout.models import horizon, minutes, points, team_goals
from fplscout.models.dataset import load_dataset
from fplscout.models.train import _team_goals_lookup, project_gw

HORIZON = 8
DECAY = 0.84
INITIAL_BUDGET = 1000  # £100.0m

# Decision-time hit cost used when calling the optimizer — a fixed margin above
# the real -4 FPL rule, per the plan's own §7 wording ("only takes a -4 when EV
# gain over horizon > 4 + margin") and review guidance to keep this as "a tunable
# safety factor, not the load-bearing fix". ACTUAL scoring always deducts the
# real -4/hit (see the `gw_score -= 4 * hits` line below) — this constant only
# raises the bar the optimizer must clear to decide a transfer is worth a hit,
# damping single-gameweek EV noise into transfer churn even after the leak-safe
# multi-step forecast (models/horizon.py). Not tuned against the backtest total;
# 6 = a fixed 50% buffer over the true cost, chosen once and left alone.
DECISION_HIT_COST = 6

# Over-trading controls (P0-A). The backtest was taking ~2 hits/week (73 and 65
# hits/season) chasing week-to-week EV noise. `TRANSFER_PENALTY` charges every
# transfer (free ones included) the option value of the banked free transfer it
# consumes; `MAX_HITS_PER_GW` hard-caps paid transfers per gameweek. The 2024-25
# sweep (data/reports/p0a_sweep.md) showed season totals are NOT sensitive to
# these within ±60pts of decision-path noise — so the values below are chosen on
# priors (published FT option-value estimates of ~1-2 pts; good managers take
# 5-10 hits/season, the cap lands us ~22-25), not on the sweep's noisy argmax.
TRANSFER_PENALTY = 1.5
MAX_HITS_PER_GW: int | None = 1


@dataclass
class SimGwResult:
    gw: int
    squad: set[int]
    starting_xi: set[int]
    captain: int | None
    effective_captain: int | None
    chip_used: str | None
    hits: int
    transfers_in: set[int]
    transfers_out: set[int]
    gw_score: int
    bank: int
    free_transfers: int


@dataclass
class SeasonResult:
    season: str
    train_seasons: list[str]
    gw_results: list[SimGwResult] = field(default_factory=list)

    @property
    def total_points(self) -> int:
        return sum(g.gw_score for g in self.gw_results)


def _actual_by_gw(con: duckdb.DuckDBPyConnection, season: str) -> dict[int, pd.DataFrame]:
    df = con.execute(
        "SELECT gw, code, SUM(total_points) AS total_points, SUM(minutes) AS minutes "
        "FROM player_gw_history WHERE season = ? GROUP BY gw, code",
        [season],
    ).df()
    return {gw: sub for gw, sub in df.groupby("gw")}


def _player_universe_by_gw(
    con: duckdb.DuckDBPyConnection, season: str
) -> dict[int, pd.DataFrame]:
    """One row per (gw, code) with position/team_id/price for every player who
    has DEBUTED by that gw — the pool the optimizer chooses from.

    Deliberately NOT restricted to players with an actual fixture that specific
    gw: a player on a blank-gameweek team (their club has no fixture, e.g. 6 of
    20 teams in 2025-26 GW34) still exists and is still ownable/sellable, they
    just contribute ~0 EV for that one gw. Forward-fills position/team/price
    from each player's last known row into gaps. Missing this was a real bug —
    a squad member whose team blanked would vanish from the optimizer's view
    entirely (not just "score 0", literally absent from its variable set),
    breaking the squad-continuity constraint and making that gw's solve
    infeasible outright. Caught because GW34 (14/20 teams played) scored
    exactly 0 with an empty starting_xi in the first backtest run.
    """
    df = con.execute(
        "SELECT gw, code, position, team_id, value AS price "
        "FROM player_gw_history WHERE season = ? "
        "GROUP BY gw, code, position, team_id, value",
        [season],
    ).df()
    max_gw = int(df["gw"].max())
    codes = df["code"].unique()
    full_index = pd.MultiIndex.from_product(
        [codes, range(1, max_gw + 1)], names=["code", "gw"]
    )
    full = (
        df.set_index(["code", "gw"])
        .reindex(full_index)
        .sort_index()
        .groupby(level=0)
        .ffill()
        .reset_index()
        .dropna(subset=["position", "team_id", "price"])
    )
    return {gw: sub for gw, sub in full.groupby("gw")}


def _selling_price(now_price: int, purchase_price: int) -> int:
    if now_price <= purchase_price:
        return now_price
    return purchase_price + (now_price - purchase_price) // 2


@dataclass
class PreparedSeason:
    """Everything simulate_season needs that does NOT depend on optimizer
    config — trained models' projections, horizon EVs, actuals, universe.
    Computing this dominates a replay's runtime, so parameter sweeps prepare
    once and replay many times."""

    season: str
    train_seasons: list[str]
    ev_by_gw: pd.DataFrame
    horizon_ev_by_gw: dict[int, pd.Series]
    position_by_code: dict[int, str]
    actual: dict[int, pd.DataFrame]
    universe: dict[int, pd.DataFrame]
    max_gw: int


def prepare_season(
    con: duckdb.DuckDBPyConnection,
    season: str,
    train_seasons: list[str],
) -> PreparedSeason:
    train_df = load_dataset(con, train_seasons)
    target_df = load_dataset(con, [season])

    minutes_model = minutes.train(train_df)
    fixtures = con.execute(
        f"SELECT * FROM fixtures WHERE season IN ({', '.join(['?'] * len(train_seasons))})",
        train_seasons,
    ).df()
    teams = con.execute("SELECT season, team_id, code FROM teams").df()
    dc_model = team_goals.fit(fixtures, teams)

    mins_proba = minutes.predict_proba(minutes_model, train_df)
    tg_lookup = _team_goals_lookup(dc_model, train_df, teams)
    train_full = points.add_model_features(train_df, mins_proba, tg_lookup)
    points_models = points.train(train_full)

    preds, target_feat = project_gw(minutes_model, dc_model, points_models, target_df, teams)
    ev_by_gw = target_feat[["gw", "code", "fixture_id"]].copy()
    ev_by_gw["ev_points"] = preds["ev_points"]

    # Leak-safe multi-step forecast for the transfer decision's total_ev (see
    # models/horizon.py): freezes player-form at each decision gw, swaps in the
    # real fixture context per target gw. Separate from ev_by_gw above, which
    # stays a single-current-gw projection used for chip planning only.
    horizon_ev_by_gw = horizon.build_horizon_ev_all_gws(
        con, minutes_model, dc_model, points_models, season, horizon=HORIZON, decay=DECAY
    )

    position_by_code = dict(zip(target_df["code"], target_df["position"], strict=False))
    actual = _actual_by_gw(con, season)
    universe = _player_universe_by_gw(con, season)
    max_gw = int(target_df["gw"].max())

    return PreparedSeason(
        season=season, train_seasons=train_seasons, ev_by_gw=ev_by_gw,
        horizon_ev_by_gw=horizon_ev_by_gw, position_by_code=position_by_code,
        actual=actual, universe=universe, max_gw=max_gw,
    )


def simulate_season(
    con: duckdb.DuckDBPyConnection,
    season: str,
    train_seasons: list[str],
    use_chips: bool = True,
    transfer_penalty: float = TRANSFER_PENALTY,
    max_hits: int | None = MAX_HITS_PER_GW,
    prepared: PreparedSeason | None = None,
) -> SeasonResult:
    if prepared is None:
        prepared = prepare_season(con, season, train_seasons)
    ev_by_gw = prepared.ev_by_gw
    horizon_ev_by_gw = prepared.horizon_ev_by_gw
    position_by_code = prepared.position_by_code
    actual = prepared.actual
    universe = prepared.universe
    max_gw = prepared.max_gw

    chip_plan: list[chip_planner.ChipRecommendation] = []
    chips_available = {"wildcard": 2, "bboost": 2, "3xc": 2}
    if use_chips:
        chip_plan = _plan_chips_for_season(ev_by_gw, universe, max_gw)

    result = SeasonResult(season=season, train_seasons=train_seasons)
    squad: set[int] = set()
    purchase_prices: dict[int, int] = {}
    bank = INITIAL_BUDGET
    free_transfers = 1

    for gw in range(1, max_gw + 1):
        if gw not in universe:
            continue
        gw_universe = universe[gw].copy()
        horizon_ev = horizon_ev_by_gw.get(gw, pd.Series(dtype=float))
        gw_universe["total_ev"] = gw_universe["code"].map(horizon_ev).fillna(0.0)
        gw_universe["price"] = gw_universe["price"].astype(int)

        is_initial_draft = gw == 1

        chip_mode = None
        planned_chip_name = None
        if not is_initial_draft:
            # GW1 is always a forced "wildcard" optimizer call for the initial
            # draft (see below) — a bboost/3xc scheduled for GW1 would otherwise
            # get silently overridden by that but still marked "used", spending
            # a real chip without ever actually applying it. Skip the lookup
            # entirely for GW1 so chips_available is only ever decremented when
            # the chip is genuinely applied.
            for rec in chip_plan:
                if rec.gw == gw and chips_available.get(rec.chip, 0) > 0:
                    planned_chip_name = rec.chip
                    chip_mode = "bench_boost" if rec.chip == "bboost" else (
                        "triple_captain" if rec.chip == "3xc" else rec.chip
                    )
                    break
        opt_input = OptimizerInput(
            projections=gw_universe,
            current_squad=squad,
            purchase_prices=purchase_prices,
            bank=bank,
            free_transfers=free_transfers,
            chip_mode="wildcard" if is_initial_draft else chip_mode,
            hit_cost=DECISION_HIT_COST,
            transfer_penalty=transfer_penalty,
            max_hits=max_hits,
        )
        opt_result = optimize(opt_input)
        if opt_result.status != "Optimal":
            # infeasible (e.g. a player disappeared from the universe) -> keep
            # the existing squad untouched this GW rather than crash the replay
            opt_result_squad = squad
            starting_xi = set()
            captain = vice_captain = None
            bench_order: list[int] = []
            bench_gk = None
            hits = 0
            buys: set[int] = set()
            sells: set[int] = set()
        else:
            opt_result_squad = opt_result.squad
            starting_xi = opt_result.starting_xi
            captain = opt_result.captain
            vice_captain = opt_result.vice_captain
            bench_order = opt_result.bench_order
            bench_gk = opt_result.bench_gk
            hits = 0 if is_initial_draft else opt_result.hits
            buys = opt_result.transfers_in
            sells = opt_result.transfers_out

        price_lookup = dict(zip(gw_universe["code"], gw_universe["price"], strict=False))
        sale_value = sum(
            _selling_price(
                price_lookup.get(c, purchase_prices.get(c, 0)), purchase_prices.get(c, 0)
            )
            for c in sells
        )
        buy_cost = sum(price_lookup.get(c, 0) for c in buys)
        bank = bank - buy_cost + sale_value
        for c in buys:
            purchase_prices[c] = price_lookup.get(c, 0)
        for c in sells:
            purchase_prices.pop(c, None)
        squad = opt_result_squad

        actual_gw = actual.get(gw, pd.DataFrame(columns=["code", "total_points", "minutes"]))
        points_by_code = dict(zip(actual_gw["code"], actual_gw["total_points"], strict=False))
        minutes_by_code = dict(zip(actual_gw["code"], actual_gw["minutes"], strict=False))

        autosub = apply_autosubs(
            starting_xi, bench_order, bench_gk, captain, vice_captain,
            minutes_by_code, position_by_code,
        )
        gw_score = 0
        for code in autosub.final_xi:
            base = points_by_code.get(code, 0)
            multiplier = 1
            if code == autosub.effective_captain:
                multiplier = 3 if chip_mode == "triple_captain" else 2
            gw_score += base * multiplier
        if chip_mode == "bench_boost":
            bench_codes = [c for c in bench_order if c is not None]
            if bench_gk is not None:
                bench_codes.append(bench_gk)
            for code in bench_codes:
                if code not in autosub.final_xi:
                    gw_score += points_by_code.get(code, 0)
        gw_score -= 4 * hits

        if is_initial_draft:
            free_transfers = 1
        elif chip_mode in ("wildcard",):
            free_transfers = min(5, free_transfers + 1)
        else:
            n_buys = len(buys)
            free_transfers = min(5, max(0, free_transfers - n_buys) + 1)

        if planned_chip_name is not None:
            chips_available[planned_chip_name] -= 1

        result.gw_results.append(
            SimGwResult(
                gw=gw, squad=set(squad), starting_xi=set(autosub.final_xi),
                captain=captain, effective_captain=autosub.effective_captain,
                chip_used=chip_mode if not is_initial_draft else None,
                hits=hits, transfers_in=set(buys), transfers_out=set(sells),
                gw_score=gw_score, bank=bank, free_transfers=free_transfers,
            )
        )

    return result


def _plan_chips_for_season(
    ev_by_gw: pd.DataFrame, universe: dict[int, pd.DataFrame], max_gw: int
) -> list[chip_planner.ChipRecommendation]:
    """Fires bench_boost/triple_captain on the LAST valid gameweek of each half
    window — deliberately not an attempt at *optimal* within-window timing.

    An earlier version picked the best gameweek within each window by comparing
    every candidate gameweek's own ev_by_gw row — which has the exact same
    look-ahead problem as the transfer-decision bug this module's docstring
    describes: a decision conceptually made "at the start of the half" was
    reading gameweek rows whose rolling-form features reflect real match
    outcomes from the gameweeks between the start of the half and that
    candidate — information not actually available yet. A genuinely walk-forward
    fix (fire when the CURRENT gameweek's own leak-safe value clears a
    training-data-derived threshold, else hold) is real follow-up work; given
    the core transfer-decision leak was the dominant driver of an inflated
    backtest score and is what this pass prioritized fixing, chip timing was
    simplified to something trivially leak-safe instead of shipping a second,
    less-tested walk-forward heuristic under the same time pressure. "Use it or
    lose it on expiry" is also genuine real-world FPL behavior, not just a
    placeholder — many managers do exactly this.
    """
    recs: list[chip_planner.ChipRecommendation] = []
    halves = [(1, min(19, max_gw)), (20, max_gw)] if max_gw > 19 else [(1, max_gw)]
    for start, stop in halves:
        if start > stop:
            continue
        gws_with_data = [
            gw for gw in range(start, stop + 1) if len(ev_by_gw[ev_by_gw["gw"] == gw]) > 0
        ]
        if not gws_with_data:
            continue
        # staggered, not simultaneous: firing both chips on the identical gw
        # would mean only the first-checked one (see simulate_season's chip_plan
        # lookup) ever actually fires, silently wasting the other every half.
        bboost_gw = gws_with_data[-1]
        xc_gw = gws_with_data[-2] if len(gws_with_data) >= 2 else gws_with_data[-1]

        bboost_top = (
            ev_by_gw[ev_by_gw["gw"] == bboost_gw].groupby("code")["ev_points"].sum()
        ).sort_values(ascending=False)
        bboost_ev = bboost_top.tail(4).sum() if len(bboost_top) >= 4 else 0.0
        recs.append(chip_planner.ChipRecommendation("bboost", bboost_gw, bboost_ev))

        xc_top = (
            ev_by_gw[ev_by_gw["gw"] == xc_gw].groupby("code")["ev_points"].sum()
        ).sort_values(ascending=False)
        captain_ev = xc_top.iloc[0] if len(xc_top) > 0 else 0.0
        recs.append(chip_planner.ChipRecommendation("3xc", xc_gw, captain_ev))
    return recs
