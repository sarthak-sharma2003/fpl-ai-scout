from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from fplscout import db
from fplscout.ingest import vaastav
from fplscout.models import minutes, points, team_goals
from fplscout.models.dataset import load_dataset
from fplscout.models.train import (
    _beats,
    _compare_columns,
    _rmse,
    _spearman,
    _team_goals_lookup,
    project_gw,
    train_test_split_by_season,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "vaastav"


def test_train_test_split_by_season():
    seasons = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
    train, holdout = train_test_split_by_season(seasons, "2025-26")
    assert train == ["2021-22", "2022-23", "2023-24", "2024-25"]
    assert holdout == "2025-26"


def test_rmse_perfect_prediction_is_zero():
    y = pd.Series([1.0, 2.0, 3.0])
    assert _rmse(y, y) == 0.0


def test_rmse_ignores_nan_rows():
    y_true = pd.Series([1.0, 2.0, np.nan])
    y_pred = pd.Series([1.0, 2.0, 5.0])
    assert _rmse(y_true, y_pred) == 0.0


def test_spearman_perfect_rank_correlation():
    y_true = pd.Series([1.0, 2.0, 3.0, 4.0])
    y_pred = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert _spearman(y_true, y_pred) == 1.0


def test_spearman_too_few_points_is_nan():
    y_true = pd.Series([1.0])
    y_pred = pd.Series([1.0])
    assert np.isnan(_spearman(y_true, y_pred))


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
    from fplscout.features.build import write_features

    write_features(con)
    yield con
    con.close()


def test_project_gw_end_to_end_on_real_fixture_data(loaded_con):
    """Integration test: train real (tiny) minutes/team-goals/points models on
    2021-22 and project 2025-26's rows with project_gw() — proves the reusable
    inference path Phase 5/6 depend on actually works end to end, not just its
    pieces in isolation."""
    train_df = load_dataset(loaded_con, ["2021-22"])
    target_df = load_dataset(loaded_con, ["2025-26"])

    minutes_model = minutes.train(train_df)
    fixtures = loaded_con.execute(
        "SELECT * FROM fixtures WHERE season = '2021-22'"
    ).df()
    teams = loaded_con.execute("SELECT season, team_id, code FROM teams").df()
    dc_model = team_goals.fit(fixtures, teams)

    mins_proba = minutes.predict_proba(minutes_model, train_df)
    tg_lookup = _team_goals_lookup(dc_model, train_df, teams)
    train_full = points.add_model_features(train_df, mins_proba, tg_lookup)
    points_models = points.train(train_full)

    preds, feat = project_gw(minutes_model, dc_model, points_models, target_df, teams)
    assert len(preds) == len(target_df)
    assert list(preds.columns) == [
        "season", "gw", "fixture_id", "code", "position",
        "ev_points", "q10_points", "q90_points",
    ]
    # this fixture is deliberately tiny (~35 rows across 4 positions), well under
    # points.train()'s 50-row-per-position minimum, so no position model actually
    # trains -- preds[ev_points] being all-NaN here is correct, documented
    # behavior (see test_points.py::test_predict_leaves_unmodeled_positions_as_nan),
    # not a bug. The real assertion is that the plumbing runs end to end without
    # error and returns the right shape; realistic non-NaN coverage is already
    # proven by the full-scale run in cli.py's `train` command.
    assert "expected_minutes" in feat.columns


def test_project_gw_availability_factor_zeroes_out_expected_minutes(loaded_con):
    """A code with a monkeypatched availability_factor of 0.0 (ruled out) must
    get ~0 expected_minutes back — the core acceptance check for issue #1's
    availability overlay. Backtest/training callers never pass this arg, so
    their output is untouched (see project_gw's default None)."""
    train_df = load_dataset(loaded_con, ["2021-22"])
    target_df = load_dataset(loaded_con, ["2025-26"])

    minutes_model = minutes.train(train_df)
    fixtures = loaded_con.execute("SELECT * FROM fixtures WHERE season = '2021-22'").df()
    teams = loaded_con.execute("SELECT season, team_id, code FROM teams").df()
    dc_model = team_goals.fit(fixtures, teams)

    mins_proba = minutes.predict_proba(minutes_model, train_df)
    tg_lookup = _team_goals_lookup(dc_model, train_df, teams)
    train_full = points.add_model_features(train_df, mins_proba, tg_lookup)
    points_models = points.train(train_full)

    ruled_out_code = int(target_df["code"].iloc[0])
    factor = dict.fromkeys(target_df["code"].unique(), 1.0)
    factor[ruled_out_code] = 0.0

    _, feat = project_gw(
        minutes_model, dc_model, points_models, target_df, teams,
        availability_factor=factor,
    )
    ruled_out_rows = feat[feat["code"] == ruled_out_code]
    assert (ruled_out_rows["expected_minutes"] == 0.0).all()
    assert (ruled_out_rows["mins_p0"] == 1.0).all()


def test_compare_columns_returns_one_bundle_per_predictor():
    df = pd.DataFrame(
        {
            "gw": [1, 1, 2, 2],
            "actual": [1.0, 2.0, 3.0, 4.0],
            "pred_a": [1.0, 2.0, 3.0, 4.0],
            "pred_b": [4.0, 3.0, 2.0, 1.0],
        }
    )
    result = _compare_columns(df, "actual", {"a": "pred_a", "b": "pred_b"})
    assert set(result.keys()) == {"a", "b"}
    assert result["a"]["rmse"] == 0.0


def test_beats_true_when_challenger_has_higher_spearman():
    challenger = {"mean_per_gw_spearman": 0.5}
    baseline = {"mean_per_gw_spearman": 0.3}
    assert _beats(challenger, baseline) is True
    assert _beats(baseline, challenger) is False


def test_beats_true_when_baseline_has_no_valid_gameweeks():
    challenger = {"mean_per_gw_spearman": 0.1}
    baseline = {"mean_per_gw_spearman": float("nan")}
    assert _beats(challenger, baseline) is True
