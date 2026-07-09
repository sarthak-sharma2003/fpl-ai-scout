from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from fplscout import db
from fplscout.backtest.simulator import _player_universe_by_gw, _selling_price
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
        _mock_season("2025-26")
        con = db.connect(":memory:")
        db.init_schema(con)
        vaastav.load_all_seasons(con, cache_dir=tmp_path / "raw", seasons=["2025-26"])
    yield con
    con.close()


def test_selling_price_profit_split_and_loss_passthrough():
    assert _selling_price(now_price=60, purchase_price=50) == 55  # +10 profit -> +5
    assert _selling_price(now_price=61, purchase_price=50) == 55  # +11 -> floor(11/2)=5
    assert _selling_price(now_price=45, purchase_price=50) == 45  # loss -> current value
    assert _selling_price(now_price=50, purchase_price=50) == 50  # flat


def test_player_universe_forward_fills_blank_gameweeks(loaded_con):
    """Regression test for a real bug: a player whose team blanked (no fixture
    that gw) used to vanish entirely from that gw's universe instead of
    carrying forward with ~0 gw contribution — breaking squad continuity for
    the optimizer. This fixture's players all appear in GW1-3; verify every
    player present in gw1 also has a row in every later gw up to the fixture's
    max gw, even though not every player has a real merged_gw row every week."""
    universe = _player_universe_by_gw(loaded_con, "2025-26")
    gw1_codes = set(universe[1]["code"])
    max_gw = max(universe)
    for gw in range(1, max_gw + 1):
        codes_this_gw = set(universe[gw]["code"])
        # every player known by gw1 must still be present (forward-filled) in
        # every subsequent gw up to the fixture's horizon, not silently dropped
        assert gw1_codes <= codes_this_gw, f"players dropped from universe at gw {gw}"


def test_player_universe_has_no_nulls(loaded_con):
    universe = _player_universe_by_gw(loaded_con, "2025-26")
    for gw, df in universe.items():
        assert not df[["position", "team_id", "price"]].isna().any().any(), gw
