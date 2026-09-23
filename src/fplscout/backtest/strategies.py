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
DIFFERENTIAL_TOP_N = 20  # "differential": never buy the N most-owned
FORM_WINDOW = 4
TOP_ATTACK_TEAMS = 6  # attackers only from the N highest-scoring clubs
XGI_TOP_N = 60  # attackers only from the N best by recent xGI
ATTACK = ("MID", "FWD")


def load_history(con: duckdb.DuckDBPyConnection, season: str) -> pd.DataFrame:
    """One row per (gw, code), DGW fixtures summed."""
    return con.execute(
        "SELECT gw, code, SUM(starts) AS starts, SUM(minutes) AS minutes, "
        "SUM(total_points) AS total_points, MAX(selected) AS selected, "
        "MAX(transfers_balance) AS transfers_balance, MAX(team_id) AS team_id, "
        "MAX(position) AS position, SUM(goals_scored) AS goals_scored, "
        "SUM(expected_goal_involvements) AS xgi, BOOL_OR(was_home) AS was_home "
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

    def differential(gw, uni):
        p = past(gw, 1)
        if p.empty:
            return uni
        top = set(p.nlargest(DIFFERENTIAL_TOP_N, "selected")["code"])
        return _and(uni, "buy_ok", set(uni["code"]) - top)

    def _attack_only(uni, ok):
        # restricts MID/FWD buys only; GK/DEF untouched
        return _and(uni, "buy_ok", ok | set(uni.loc[~uni["position"].isin(ATTACK), "code"]))

    def top_attack_teams(gw, uni):
        h = history[(history["gw"] < gw) & (history["gw"] >= gw - 6)]
        if h.empty:
            return uni
        teams = h.groupby("team_id")["goals_scored"].sum().nlargest(TOP_ATTACK_TEAMS).index
        return _attack_only(uni, set(uni.loc[uni["team_id"].isin(teams), "code"]))

    def xgi_top(gw, uni):
        h = history[(history["gw"] < gw) & (history["gw"] >= gw - FORM_WINDOW)]
        if h.empty:
            return uni
        xgi = h[h["position"].isin(ATTACK)].groupby("code")["xgi"].sum()
        return _attack_only(uni, set(xgi.nlargest(XGI_TOP_N).index))

    def home_captain(gw, uni):
        # was_home of the decision GW's own fixture is known before the deadline
        home = set(history.loc[(history["gw"] == gw) & history["was_home"], "code"])
        uni["cap_rank"] = uni["cap_ev"] - 100 * ~uni["code"].isin(home)
        return uni

    def form_captain(gw, uni):
        h = history[(history["gw"] < gw) & (history["gw"] >= gw - FORM_WINDOW)]
        if h.empty:
            return uni
        uni["cap_rank"] = uni["code"].map(h.groupby("code")["total_points"].sum()).fillna(0)
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
        "differential": differential,
        "top_attack_teams": top_attack_teams,
        "xgi_top": xgi_top,
        "home_captain": home_captain,
        "form_captain": form_captain,
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
    # round 2
    "differential (skip top-20 owned)": ["differential"],
    "attackers from top-6 scoring clubs": ["top_attack_teams"],
    "attackers top-60 by xGI": ["xgi_top"],
    "home captain": ["home_captain"],
    "form captain": ["form_captain"],
    "started + template + xGI": ["started_last", "template", "xgi_top"],
    "started + top-6 attack": ["started_last", "top_attack_teams"],
    "started + template + home captain": ["started_last", "template", "home_captain"],
}
