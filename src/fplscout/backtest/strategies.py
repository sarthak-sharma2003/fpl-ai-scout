"""Human-style FPL strategies as rules layered on the backtest simulator.

A rule is `rule(gw, universe) -> universe` and may AND into two bool columns,
`buy_ok` (may be bought) and `start_ok` (may start), overwrite `total_ev` /
`cap_ev`, or set `cap_rank` (captain = highest in the XI, overriding the
model). Strategies are lists of rules, so combinations are concatenation.

Leak-safe: every rule reads only history rows with gw < the decision gw.
GW1 has no in-season history, so filters are no-ops there.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from fplscout.backtest.simulator import DECAY, HORIZON

TEMPLATE_TOP_N = 150  # "template" pool: the N most-owned players last GW
FORM_WINDOW = 4


def load_history(con: duckdb.DuckDBPyConnection, season: str) -> pd.DataFrame:
    """One row per (gw, code), DGW fixtures summed."""
    return con.execute(
        "SELECT gw, code, SUM(starts) AS starts, SUM(minutes) AS minutes, "
        "SUM(total_points) AS total_points, MAX(selected) AS selected, "
        "MAX(transfers_balance) AS transfers_balance "
        "FROM player_gw_history WHERE season = ? GROUP BY gw, code",
        [season],
    ).df()


def _and(uni: pd.DataFrame, col: str, ok_codes: set[int]) -> pd.DataFrame:
    uni[col] = uni.get(col, True) & uni["code"].isin(ok_codes)
    return uni


def make_rules(history: pd.DataFrame) -> dict[str, callable]:
    def past(gw: int, n: int) -> pd.DataFrame:
        """Each player's last n rows before gw (his last n club gameweeks)."""
        h = history[history["gw"] < gw].sort_values("gw")
        return h.groupby("code").tail(n)

    def started_last(gw, uni):
        p = past(gw, 1)
        if p.empty:
            return uni
        ok = set(p.loc[p["starts"] > 0, "code"])
        return _and(_and(uni, "buy_ok", ok), "start_ok", ok)

    def nailed3(gw, uni):
        p = past(gw, 3)
        if p.empty:
            return uni
        full = p.groupby("code").agg(n=("gw", "size"), low=("minutes", "min"))
        ok = set(full.index[(full["low"] >= 60) & (full["n"] == 3)])
        return _and(_and(uni, "buy_ok", ok), "start_ok", ok)

    def template(gw, uni):
        p = past(gw, 1)
        if p.empty:
            return uni
        return _and(uni, "buy_ok", set(p.nlargest(TEMPLATE_TOP_N, "selected")["code"]))

    def momentum(gw, uni):
        p = past(gw, 1)
        if p.empty:
            return uni
        return _and(uni, "buy_ok", set(p.loc[p["transfers_balance"] > 0, "code"]))

    def form_ev(gw, uni):
        # Replaces the model: points per GW over the last FORM_WINDOW GWs,
        # scaled to the model's horizon so hit/penalty thresholds mean the same.
        h = history[(history["gw"] < gw) & (history["gw"] >= gw - FORM_WINDOW)]
        if h.empty:
            return uni
        form = h.groupby("code")["total_points"].sum() / FORM_WINDOW
        f = uni["code"].map(form).fillna(0.0)
        uni["cap_ev"] = f
        uni["total_ev"] = f * sum(DECAY**k for k in range(HORIZON))
        return uni

    def premium_captain(gw, uni):
        uni["cap_rank"] = uni["price"]
        return uni

    return {
        "started_last": started_last,
        "nailed3": nailed3,
        "template": template,
        "momentum": momentum,
        "form_ev": form_ev,
        "premium_captain": premium_captain,
    }


STRATEGIES: dict[str, list[str]] = {
    "baseline (model)": [],
    "started last game": ["started_last"],
    "nailed: 60+ mins x3": ["nailed3"],
    "template top-150": ["template"],
    "momentum (net transfers in)": ["momentum"],
    "form chaser (no model)": ["form_ev"],
    "premium captain": ["premium_captain"],
    "started + template": ["started_last", "template"],
    "nailed3 + template": ["nailed3", "template"],
    "started + premium captain": ["started_last", "premium_captain"],
    "form + started": ["form_ev", "started_last"],
}
