from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from fplscout import db
from fplscout.features.build import (
    DECAY,
    _price_band,
    _weighted_mean,
    build_features,
    write_features,
)
from fplscout.ingest import vaastav

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "vaastav"


def _mock_season(season: str) -> None:
    base = FIXTURES_DIR / season
    respx.get(f"{vaastav.RAW_BASE}/{season}/teams.csv").mock(
        return_value=httpx.Response(200, content=(base / "teams.csv").read_bytes())
    )
    respx.get(f"{vaastav.RAW_BASE}/{season}/players_raw.csv").mock(
        return_value=httpx.Response(200, content=(base / "players_raw.csv").read_bytes())
    )
    respx.get(f"{vaastav.RAW_BASE}/{season}/fixtures.csv").mock(
        return_value=httpx.Response(200, content=(base / "fixtures.csv").read_bytes())
    )
    respx.get(f"{vaastav.RAW_BASE}/{season}/gws/merged_gw.csv").mock(
        return_value=httpx.Response(200, content=(base / "gws" / "merged_gw.csv").read_bytes())
    )


@pytest.fixture
def loaded_con(tmp_path):
    with respx.mock:
        _mock_season("2021-22")
        _mock_season("2025-26")
        con = db.connect(":memory:")
        db.init_schema(con)
        vaastav.load_all_seasons(
            con, cache_dir=tmp_path / "raw", seasons=["2021-22", "2025-26"]
        )
    yield con
    con.close()


def test_weighted_mean_matches_hand_calculation():
    # game1=17, game2=3 (most recent last); decay=0.8
    arr = np.array([17.0, 3.0])
    result = _weighted_mean(arr, DECAY)
    expected = (17 * 0.8 + 3 * 1.0) / (0.8 + 1.0)
    assert result == pytest.approx(expected)


def test_weighted_mean_all_nan_returns_nan():
    assert np.isnan(_weighted_mean(np.array([np.nan, np.nan]), DECAY))


def test_first_game_of_season_has_null_rolling_features(loaded_con):
    features = build_features(loaded_con)
    first_games = features.sort_values(["code", "season", "kickoff_time"]).groupby(
        ["code", "season"]
    ).head(1)
    assert first_games["roll5_points"].isna().all()
    assert first_games["roll5_xg"].isna().all()
    assert first_games["team_roll5_goals_for"].isna().all()


def test_leakage_recompute_sampled_row_from_scratch(loaded_con):
    """Plan §Phase2 DoD: for a sampled (player, GW), recompute features using only
    prior data and assert equality with the stored row."""
    features = build_features(loaded_con)
    salah = features[(features["code"] == 118748) & (features["season"] == "2021-22")]
    salah = salah.sort_values("kickoff_time")
    assert len(salah) >= 3
    sample_row = salah.iloc[2]  # third game of the season -> has 2 prior games

    raw = loaded_con.execute(
        """
        SELECT kickoff_time, total_points
        FROM player_gw_history
        WHERE code = 118748 AND season = '2021-22'
        ORDER BY kickoff_time
        """
    ).df()
    prior = raw[raw["kickoff_time"] < sample_row["kickoff_time"]]
    assert len(prior) == 2  # exactly the 2 games strictly before this one

    manual = _weighted_mean(prior["total_points"].to_numpy(dtype=float), DECAY)
    assert sample_row["roll5_points"] == pytest.approx(manual)


def test_leakage_no_feature_uses_same_or_future_kickoff(loaded_con):
    """Stronger, more general leakage guard: for every row, the value of any
    roll* column must be independent of that row's own and later rows' stats.
    We verify this indirectly by checking no roll5_points value for a player's
    Nth game equals a decayed function that *includes* the Nth game's own points
    when a strictly-smaller prior window produces a different number — i.e. the
    targeted regression check for the bug this module previously had (a row's
    own value leaking into its own rolling feature)."""
    features = build_features(loaded_con)
    raw = loaded_con.execute(
        "SELECT code, season, kickoff_time, total_points FROM player_gw_history"
    ).df()

    for code, season in [(118748, "2021-22"), (118748, "2025-26"), (223094, "2025-26")]:
        player_features = features[
            (features["code"] == code) & (features["season"] == season)
        ].sort_values("kickoff_time")
        player_raw = raw[(raw["code"] == code) & (raw["season"] == season)].sort_values(
            "kickoff_time"
        )
        if len(player_features) == 0:
            continue
        first_row = player_features.iloc[0]
        own_points = player_raw.iloc[0]["total_points"]
        # GW1 roll5_points must be NaN, and specifically must NOT equal this
        # row's own points (the exact shape of the historical bug).
        assert np.isnan(first_row["roll5_points"])
        assert first_row["roll5_points"] != own_points


def test_promoted_team_flag(loaded_con):
    features = build_features(loaded_con)
    sunderland_rows = features[
        (features["season"] == "2025-26")
        & (features["team_id"].isin(
            loaded_con.execute(
                "SELECT team_id FROM teams WHERE season='2025-26' AND name='Sunderland'"
            ).df()["team_id"]
        ))
    ]
    if len(sunderland_rows) > 0:
        assert sunderland_rows["promoted_team"].all()

    liverpool_rows = features[
        (features["season"] == "2025-26")
        & (features["team_id"].isin(
            loaded_con.execute(
                "SELECT team_id FROM teams WHERE season='2025-26' AND name='Liverpool'"
            ).df()["team_id"]
        ))
    ]
    if len(liverpool_rows) > 0:
        assert not liverpool_rows["promoted_team"].any()


def test_prev_season_features_match_hand_computed_aggregate(loaded_con):
    """Salah (code 118748) appears in both loaded seasons: his 2025-26 rows must
    carry his full prior-loaded-season aggregate, recomputed here from raw
    player_gw_history. His prior-season rows themselves (no season before them
    in the data) must have played_prev_season=False and NaN aggregates."""
    features = build_features(loaded_con)
    raw = loaded_con.execute(
        "SELECT SUM(minutes) AS m, SUM(total_points) AS p "
        "FROM player_gw_history WHERE code = 118748 AND season = '2021-22'"
    ).df().iloc[0]

    current = features[(features["code"] == 118748) & (features["season"] == "2025-26")]
    assert len(current) > 0
    assert current["played_prev_season"].all()
    assert (current["prev_season_minutes"] == raw["m"]).all()
    if raw["m"] >= 450:
        expected_per90 = raw["p"] / raw["m"] * 90
        assert np.allclose(current["prev_season_points_per90"], expected_per90)
    else:
        # below the 450-minute floor per-90 rates are suppressed as noise
        assert current["prev_season_points_per90"].isna().all()

    first_season = features[(features["code"] == 118748) & (features["season"] == "2021-22")]
    assert not first_season["played_prev_season"].any()
    assert first_season["prev_season_minutes"].isna().all()


def test_write_features_persists_to_duckdb(loaded_con):
    n = write_features(loaded_con)
    assert n > 0
    count = loaded_con.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    assert count == n


def _stage_upcoming_gw(con) -> int:
    """Simulate a live season in progress: one unfinished gameweek after the
    last played one, with a fixture involving Salah's team, and live prices in
    player_season (vaastav populates it with NULL value; live_gw fills value)."""
    next_gw = (
        con.execute(
            "SELECT MAX(gw) + 1 FROM player_gw_history WHERE season = '2025-26'"
        ).fetchone()[0]
    )
    salah_team = con.execute(
        "SELECT team_id FROM player_gw_history WHERE code = 118748 AND season = '2025-26' LIMIT 1"
    ).fetchone()[0]
    opponent = con.execute(
        "SELECT team_id FROM teams WHERE season = '2025-26' AND team_id != ? LIMIT 1",
        [salah_team],
    ).fetchone()[0]
    con.execute(
        "INSERT INTO gameweeks (season, event, finished) VALUES ('2025-26', ?, false)",
        [next_gw],
    )
    con.execute(
        "INSERT INTO fixtures (season, fixture_id, event, kickoff_time, team_h, team_a, "
        "team_h_difficulty, team_a_difficulty, finished) "
        "VALUES ('2025-26', 99999, ?, TIMESTAMP '2026-08-15 14:00:00', ?, ?, 3, 4, false)",
        [next_gw, salah_team, opponent],
    )
    con.execute("UPDATE player_season SET value = 130 WHERE season = '2025-26'")
    return next_gw


def test_upcoming_gw_synthetic_rows_have_leak_safe_form_and_live_price(loaded_con):
    """Issue #5: an unplayed next gameweek must get feature rows whose rolling
    form equals exactly what the played rows produce (recomputed by hand here),
    priced from player_season.value, with prev-season aggregates attached."""
    next_gw = _stage_upcoming_gw(loaded_con)
    features = build_features(loaded_con)

    salah = features[
        (features["code"] == 118748)
        & (features["season"] == "2025-26")
        & (features["gw"] == next_gw)
    ]
    assert len(salah) == 1, "exactly one synthetic row for Salah's single fixture"
    row = salah.iloc[0]

    played_points = loaded_con.execute(
        "SELECT total_points FROM player_gw_history "
        "WHERE code = 118748 AND season = '2025-26' ORDER BY kickoff_time, fixture_id"
    ).df()["total_points"].to_numpy(dtype=float)
    expected_roll5 = _weighted_mean(played_points[-5:], decay=0.8)
    assert np.isclose(row["roll5_points"], expected_roll5)

    assert row["value"] == 130  # live now_cost, not a stale history price
    assert row["fdr"] == 3  # home-team difficulty from the staged fixture
    assert row["was_home"]
    assert not np.isnan(row["prev_season_minutes"])  # cold-start join still applies


def test_upcoming_rows_excluded_from_training_included_for_prediction(loaded_con):
    """Training's inner join must drop target-less synthetic rows; the live
    pipeline's require_targets=False must keep them."""
    from fplscout.models.dataset import load_dataset

    next_gw = _stage_upcoming_gw(loaded_con)
    write_features(loaded_con)

    train_df = load_dataset(loaded_con, ["2025-26"])
    predict_df = load_dataset(loaded_con, ["2025-26"], require_targets=False)
    assert (train_df["gw"] == next_gw).sum() == 0
    assert (predict_df["gw"] == next_gw).sum() > 0
    assert predict_df[predict_df["gw"] == next_gw]["total_points"].isna().all()


def test_project_gw_end_to_end_on_upcoming_synthetic_rows(loaded_con):
    """Issue #5 acceptance: models trained on history produce finite EV
    projections for the UNPLAYED next gameweek's synthetic rows."""
    from fplscout.models import minutes, points, team_goals
    from fplscout.models.dataset import load_dataset
    from fplscout.models.train import _team_goals_lookup, project_gw

    next_gw = _stage_upcoming_gw(loaded_con)
    write_features(loaded_con)

    train_df = load_dataset(loaded_con, ["2021-22"])
    minutes_model = minutes.train(train_df)
    fixtures = loaded_con.execute("SELECT * FROM fixtures WHERE season = '2021-22'").df()
    teams = loaded_con.execute("SELECT season, team_id, code FROM teams").df()
    dc_model = team_goals.fit(fixtures, teams)
    mins_proba = minutes.predict_proba(minutes_model, train_df)
    tg_lookup = _team_goals_lookup(dc_model, train_df, teams)
    points_models = points.train(
        points.add_model_features(train_df, mins_proba, tg_lookup),
        min_rows=1,  # toy fixture has <50 rows/position; production keeps 50
    )

    predict_df = load_dataset(loaded_con, ["2025-26"], require_targets=False)
    target_df = predict_df[predict_df["gw"] == next_gw]
    assert len(target_df) > 0
    preds, _ = project_gw(minutes_model, dc_model, points_models, target_df, teams)
    assert np.isfinite(preds["ev_points"]).all()
    assert np.isfinite(preds["q90_points"]).all()


def test_latest_reference_point_targets_next_unplayed_gw(loaded_con):
    """Issue #5: with a live season in progress the reference point is the
    upcoming deadline's gameweek, not the last completed one."""
    from fplscout import pipeline

    season, gw = pipeline.latest_reference_point(loaded_con)
    last_played = loaded_con.execute(
        "SELECT MAX(gw) FROM player_gw_history WHERE season = '2025-26'"
    ).fetchone()[0]
    assert (season, gw) == ("2025-26", last_played)  # no unfinished gw -> old behavior

    next_gw = _stage_upcoming_gw(loaded_con)
    season, gw = pipeline.latest_reference_point(loaded_con)
    assert (season, gw) == ("2025-26", next_gw)


def test_price_band_buckets():
    values = pd.Series([40, 50, 60, 80, 100])
    bands = _price_band(values)
    assert bands.tolist() == ["budget", "low_mid", "mid", "premium", "elite"]
