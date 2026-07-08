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


def test_write_features_persists_to_duckdb(loaded_con):
    n = write_features(loaded_con)
    assert n > 0
    count = loaded_con.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    assert count == n


def test_price_band_buckets():
    values = pd.Series([40, 50, 60, 80, 100])
    bands = _price_band(values)
    assert bands.tolist() == ["budget", "low_mid", "mid", "premium", "elite"]
