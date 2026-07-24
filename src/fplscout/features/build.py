"""Feature store builder: player_gw_history -> features (plan §Phase 2).

No-leakage rule, enforced by construction: every rolling/team-form column for a row
is computed from a `.shift(1)` series before the rolling window is applied, so the
row currently being featurized is never included in its own rolling stats, and
nothing with a later kickoff_time can leak backwards. See `test_leakage` for the
row-level proof this DoD requires.

Deviation from the plan's feature list, found while grounding against real data:
`status` / `chance_of_playing_next_round` / `penalties_order` etc. DO exist in
vaastav's per-season `players_raw.csv`, but that file is a single end-of-data
snapshot, not a per-gameweek-as-of-deadline history. Using it as a per-GW feature
for historical rows would leak future injury/rotation information into earlier
gameweeks. So historical "availability" is instead a leak-safe proxy derived from
realized rolling `starts` (roll5_started_share) rather than the live status field.
True live status/chance_of_playing/set-piece-order features will only be populated
once the current-season live-API ingestion path exists (Phase 9) — this table's
columns intentionally don't reserve space for them; they'd apply to a different,
future data source.

Rolling windows reset at each season boundary (a player's GW1 rolling features are
NULL) rather than carrying over — simpler and avoids conflating form across a
team change or a full off-season gap.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

WINDOWS = (3, 5, 10)
DECAY = 0.8

ROLL_STATS = [
    "points",
    "minutes",
    "xg",
    "xa",
    "xgi",
    "xgc",
    "bps",
    "saves",
    "goals_conceded",
    "defensive_contribution",
    "cbit",
    "recoveries",
    "tackles",
]

_SOURCE_COLUMN = {
    "points": "total_points",
    "minutes": "minutes",
    "xg": "expected_goals",
    "xa": "expected_assists",
    "xgi": "expected_goal_involvements",
    "xgc": "expected_goals_conceded",
    "bps": "bps",
    "saves": "saves",
    "goals_conceded": "goals_conceded",
    "defensive_contribution": "defensive_contribution",
    "cbit": "clearances_blocks_interceptions",
    "recoveries": "recoveries",
    "tackles": "tackles",
    "starts": "starts",
}


def _weighted_mean(arr: np.ndarray, decay: float) -> float:
    valid = ~np.isnan(arr)
    if not valid.any():
        return np.nan
    n = len(arr)
    weights = decay ** np.arange(n - 1, -1, -1)
    return float(np.sum(arr[valid] * weights[valid]) / np.sum(weights[valid]))


def _rolling_decayed_from_shifted(already_shifted: pd.Series, window: int,
                                   decay: float = DECAY) -> pd.Series:
    """Weighted rolling mean over up to `window` values, most-recent weighted highest
    (decay**0), older values decaying by `decay` per step back.

    CONTRACT: `already_shifted` must have been produced by `.shift(1)` (or
    equivalent) before this is called — this function applies no shift of its own.
    Calling it on an unshifted series leaks the current row's own value into its
    own "rolling" feature (caught by test_leakage.py — this was a real bug during
    development, not a hypothetical one: Salah's GW1 2021-22 roll5_points came out
    as exactly his GW1 actual points, 17.0, before this contract was enforced).
    """
    return already_shifted.rolling(window, min_periods=1).apply(
        lambda a: _weighted_mean(a, decay), raw=True
    )


def _build_player_rolling(gw: pd.DataFrame) -> pd.DataFrame:
    """gw must be sorted by (code, season, kickoff_time, fixture_id)."""
    out = {}
    group_keys = [gw["code"], gw["season"]]
    for stat in [*ROLL_STATS, "starts"]:
        src = _SOURCE_COLUMN[stat]
        # per-group shift(1): NaN at each player-season's first row, so nothing
        # can ever roll its own value into itself.
        shifted = gw.groupby(["code", "season"], sort=False)[src].shift(1)
        if stat == "starts":
            out["roll5_started_share"] = shifted.groupby(group_keys, sort=False).apply(
                lambda s: s.rolling(5, min_periods=1).mean()
            ).reset_index(level=[0, 1], drop=True)
            continue
        for window in WINDOWS:
            col = f"roll{window}_{stat}"
            out[col] = shifted.groupby(group_keys, sort=False).apply(
                lambda s, w=window: _rolling_decayed_from_shifted(s, w)
            ).reset_index(level=[0, 1], drop=True)

    result = pd.DataFrame(out, index=gw.index)

    for stat in ("xg", "xa", "xgi", "bps"):
        minutes5 = result["roll5_minutes"]
        with np.errstate(invalid="ignore", divide="ignore"):
            per90 = np.where(minutes5 > 0, result[f"roll5_{stat}"] / minutes5 * 90, np.nan)
        result[f"roll5_{stat}_per90"] = per90

    return result


def _build_team_rolling(fixtures: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, team_id, fixture_id) with that team's rolling
    goals-for/against over strictly prior fixtures in the same season."""
    home = fixtures[["season", "fixture_id", "kickoff_time", "team_h", "team_a",
                      "team_h_score", "team_a_score"]].copy()
    home = home.rename(columns={"team_h": "team_id", "team_h_score": "goals_for",
                                 "team_a_score": "goals_against"})
    away = fixtures[["season", "fixture_id", "kickoff_time", "team_h", "team_a",
                      "team_h_score", "team_a_score"]].copy()
    away = away.rename(columns={"team_a": "team_id", "team_a_score": "goals_for",
                                 "team_h_score": "goals_against"})
    home = home.drop(columns=["team_a"])
    away = away.drop(columns=["team_h"])
    long = pd.concat([home, away], ignore_index=True)
    long = long.sort_values(["team_id", "season", "kickoff_time", "fixture_id"])

    group_keys = [long["team_id"], long["season"]]
    shifted_for = long.groupby(["team_id", "season"], sort=False)["goals_for"].shift(1)
    shifted_against = long.groupby(["team_id", "season"], sort=False)["goals_against"].shift(1)
    long["team_roll5_goals_for"] = shifted_for.groupby(group_keys, sort=False).apply(
        lambda s: _rolling_decayed_from_shifted(s, 5)
    ).reset_index(level=[0, 1], drop=True)
    long["team_roll5_goals_against"] = shifted_against.groupby(group_keys, sort=False).apply(
        lambda s: _rolling_decayed_from_shifted(s, 5)
    ).reset_index(level=[0, 1], drop=True)
    return long[["season", "team_id", "fixture_id", "team_roll5_goals_for",
                 "team_roll5_goals_against"]]


def _price_band(value: pd.Series) -> pd.Series:
    # value is price * 10 (e.g. 55 == £5.5m)
    bins = [-np.inf, 45, 55, 70, 90, np.inf]
    labels = ["budget", "low_mid", "mid", "premium", "elite"]
    return pd.cut(value, bins=bins, labels=labels).astype(str)


def _upcoming_universe(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One outcome-less row per (player, fixture) for the next UNFINISHED
    gameweek of the live season (issue #5): the rows `project`/`optimize` need
    to decide the upcoming deadline, which player_gw_history can't provide
    because the gameweek hasn't been played.

    Concatenated onto the played frame BEFORE the rolling computation, so the
    existing .shift(1) machinery yields exactly the right leak-safe form for
    them (all-NaN rolling + prev_season_* at a fresh season's GW1; real rolling
    form mid-season) — no new leak surface, the rows carry no outcomes at all.

    Gate: the `gameweeks` table (bootstrap events, synced by refresh) has an
    unfinished event. Fully-finished seasons — every historical/backtest DB —
    have none, so this returns empty and behavior is byte-identical there.
    Player universe/prices come from `player_season`, populated for the live
    season by ingest/live_gw.py from bootstrap (value = now_cost)."""
    nxt = con.execute(
        "SELECT season, MIN(event) FROM gameweeks WHERE NOT finished "
        "GROUP BY season ORDER BY season DESC LIMIT 1"
    ).fetchone()
    if nxt is None:
        return pd.DataFrame()
    season, event = nxt
    fixtures = con.execute(
        "SELECT season, fixture_id, event AS gw, kickoff_time, team_h, team_a "
        "FROM fixtures WHERE season = ? AND event = ? AND NOT finished",
        [season, event],
    ).df()
    players = con.execute(
        "SELECT season, element_id, code, team_id, position, value "
        "FROM player_season WHERE season = ? AND value IS NOT NULL",
        [season],
    ).df()
    if len(fixtures) == 0 or len(players) == 0:
        return pd.DataFrame()

    cols = ["season", "fixture_id", "gw", "kickoff_time", "team_id", "opponent_team_id"]
    home = fixtures.rename(columns={"team_h": "team_id", "team_a": "opponent_team_id"})
    home = home[cols].assign(was_home=True)
    away = fixtures.rename(columns={"team_a": "team_id", "team_h": "opponent_team_id"})
    away = away[cols].assign(was_home=False)
    team_fixtures = pd.concat([home, away], ignore_index=True)
    return players.merge(team_fixtures, on=["season", "team_id"], how="inner")


def build_features(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    gw = con.execute(
        """
        SELECT season, gw, fixture_id, code, element_id, team_id, opponent_team_id,
               was_home, position, kickoff_time, value, starts,
               total_points, minutes, expected_goals, expected_assists,
               expected_goal_involvements, expected_goals_conceded, bps, saves,
               goals_conceded, defensive_contribution, clearances_blocks_interceptions,
               recoveries, tackles
        FROM player_gw_history
        ORDER BY code, season, kickoff_time, fixture_id
        """
    ).df()

    # upcoming-GW synthetic rows (issue #5): outcome columns become NaN on
    # concat; re-sort restores _build_player_rolling's ordering contract
    upcoming = _upcoming_universe(con)
    if len(upcoming) > 0:
        gw = pd.concat([gw, upcoming], ignore_index=True)
        gw = gw.sort_values(["code", "season", "kickoff_time", "fixture_id"]).reset_index(
            drop=True
        )

    teams = con.execute(
        # CAST: strength is NULL pre-season (FPL seeds it later), and duckdb
        # returns nullable Int32 for it — an extension dtype LightGBM rejects.
        # DOUBLE → plain float64 with NaN, which the trees handle natively.
        "SELECT season, team_id, CAST(strength AS DOUBLE) AS strength FROM teams"
    ).df()
    fixtures = con.execute(
        """
        SELECT season, fixture_id, kickoff_time, team_h, team_a, team_h_score,
               team_a_score, team_h_difficulty, team_a_difficulty
        FROM fixtures
        """
    ).df()

    rolling = _build_player_rolling(gw)
    features = pd.concat([gw, rolling], axis=1)

    # fixture difficulty (own team's FDR for this fixture) + opponent strength
    fdr = fixtures[["season", "fixture_id", "team_h_difficulty", "team_a_difficulty"]]
    features = features.merge(fdr, on=["season", "fixture_id"], how="left")
    features["fdr"] = np.where(
        features["was_home"], features["team_h_difficulty"], features["team_a_difficulty"]
    )
    features = features.drop(columns=["team_h_difficulty", "team_a_difficulty"])

    opp_strength = teams.rename(
        columns={"team_id": "opponent_team_id", "strength": "opponent_strength"}
    )
    features = features.merge(opp_strength, on=["season", "opponent_team_id"], how="left")

    # rest days: days since this player's previous fixture, this season
    grouped = features.sort_values(
        ["code", "season", "kickoff_time", "fixture_id"]
    ).groupby(["code", "season"], sort=False)
    prev_kickoff = grouped["kickoff_time"].shift(1)
    features = features.sort_values(["code", "season", "kickoff_time", "fixture_id"])
    features["rest_days"] = (
        pd.to_datetime(features["kickoff_time"]) - pd.to_datetime(prev_kickoff)
    ).dt.total_seconds() / 86400

    # DGW flag: this player's team played >1 fixture in this gw
    team_gw_counts = (
        features.groupby(["season", "gw", "team_id"])["fixture_id"].transform("nunique")
    )
    features["is_dgw"] = team_gw_counts > 1

    # team form
    team_rolling = _build_team_rolling(fixtures)
    features = features.merge(
        team_rolling, on=["season", "team_id", "fixture_id"], how="left"
    )

    # share-of-team (5-game window)
    for stat in ("xg", "xa", "xgi"):
        team_sum = features.groupby(["season", "team_id", "gw"])[f"roll5_{stat}"].transform(
            "sum"
        )
        with np.errstate(invalid="ignore", divide="ignore"):
            features[f"roll5_{stat}_share"] = np.where(
                team_sum > 0, features[f"roll5_{stat}"] / team_sum, np.nan
            )

    # promoted-team flag: team wasn't present in this season's *previous* season
    all_seasons = sorted(features["season"].unique())
    season_prev = dict(zip(all_seasons[1:], all_seasons[:-1], strict=True))
    prior_teams = {
        s: set(teams.loc[teams["season"] == s, "team_id"]) for s in all_seasons
    }
    features["promoted_team"] = features.apply(
        lambda r: r["season"] in season_prev
        and r["team_id"] not in prior_teams.get(season_prev.get(r["season"], ""), set()),
        axis=1,
    )

    # prior-season aggregates joined on the persistent `code` (season cold-start):
    # rolling windows deliberately reset each season, so without these every
    # player opens a season on all-NaN form and a new signing is invisible until
    # ~GW5. A full prior season is strictly in the past relative to every row of
    # the current season — leak-safe by construction. "Previous" means the
    # previous *loaded* season (same convention as promoted_team above).
    season_totals = gw.groupby(["code", "season"], as_index=False).agg(
        prev_season_minutes=("minutes", "sum"),
        _points=("total_points", "sum"),
        _xgi=("expected_goal_involvements", "sum"),
        _starts=("starts", "sum"),
    )
    next_season = {prev: cur for cur, prev in season_prev.items()}
    season_totals["season"] = season_totals["season"].map(next_season)
    season_totals = season_totals.dropna(subset=["season"])
    mins = season_totals["prev_season_minutes"]
    # per-90 rates are noise below ~5 full games; keep raw minutes either way
    with np.errstate(invalid="ignore", divide="ignore"):
        season_totals["prev_season_points_per90"] = np.where(
            mins >= 450, season_totals["_points"] / mins * 90, np.nan
        )
        season_totals["prev_season_xgi_per90"] = np.where(
            mins >= 450, season_totals["_xgi"] / mins * 90, np.nan
        )
    season_totals["prev_season_starts_share"] = season_totals["_starts"] / 38.0
    season_totals["played_prev_season"] = True
    season_totals = season_totals.drop(columns=["_points", "_xgi", "_starts"])
    features = features.merge(season_totals, on=["code", "season"], how="left")
    features["played_prev_season"] = (
        features["played_prev_season"].astype("boolean").fillna(False).astype(bool)
    )

    features["price_band"] = _price_band(features["value"])

    output_cols = [
        "season", "gw", "fixture_id", "code", "team_id", "opponent_team_id",
        "was_home", "position", "kickoff_time", "value", "price_band", "promoted_team",
        *[f"roll{w}_{stat}" for stat in ROLL_STATS for w in WINDOWS],
        "roll5_xg_per90", "roll5_xa_per90", "roll5_xgi_per90", "roll5_bps_per90",
        "roll5_xg_share", "roll5_xa_share", "roll5_xgi_share",
        "fdr", "opponent_strength", "rest_days", "is_dgw",
        "team_roll5_goals_for", "team_roll5_goals_against",
        "roll5_started_share",
        "prev_season_minutes", "prev_season_points_per90", "prev_season_xgi_per90",
        "prev_season_starts_share", "played_prev_season",
    ]
    return features[output_cols]


def write_features(con: duckdb.DuckDBPyConnection) -> int:
    features_df = build_features(con)  # noqa: F841 (used via DuckDB replacement scan below)
    con.execute("DELETE FROM features")
    con.execute("INSERT INTO features BY NAME SELECT * FROM features_df")
    return len(features_df)
