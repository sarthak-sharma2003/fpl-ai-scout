"""Pre-publish sanity gate — the "no silent garbage" layer.

Validates the latest recommendation against the FPL rulebook and basic
model-output sanity BEFORE it reaches the site or a real deadline decision.
Every check here corresponds to a failure mode that has either actually
happened in this repo (NULL availability zeroing all minutes — caught by the
dress rehearsal), or would be an embarrassing/point-losing way to discover a
bug on deadline day (illegal squad, flagged captain, stale projections).

Pure read-only: `run_preflight` returns findings; cli.py decides exit codes,
publish.py refuses to publish on FAIL (unless --force).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb
import pandas as pd

from fplscout.decide.optimizer import SQUAD_COUNTS, SQUAD_SIZE, XI_BOUNDS, XI_SIZE
from fplscout.publish import _reference_frame

BUDGET_TENTHS = 1000  # 100.0m initial budget, price units are tenths
STALE_PROJECTION_HOURS = 48
# statuses: a=available, d=doubtful; i/s/u/n = injured/suspended/unavailable/
# not in squad — none of the latter should ever start.
HARD_OUT_STATUSES = {"i", "s", "u", "n"}


@dataclass
class Finding:
    level: str  # "FAIL" | "WARN"
    check: str
    detail: str


def _findings_frame(con: duckdb.DuckDBPyConnection, season: str, gw: int) -> pd.DataFrame:
    # _reference_frame already carries status/news/chance columns (publish needs
    # them for player-card flags) — no extra join required here
    return _reference_frame(con, season, gw)


def run_preflight(
    con: duckdb.DuckDBPyConnection, season: str, gw: int, now: datetime | None = None
) -> list[Finding]:
    now = now or datetime.now(UTC)
    findings: list[Finding] = []
    fail = lambda check, detail: findings.append(Finding("FAIL", check, detail))  # noqa: E731
    warn = lambda check, detail: findings.append(Finding("WARN", check, detail))  # noqa: E731

    rec = con.execute(
        "SELECT squad, starting_xi, captain_code, vice_captain_code, chip, generated_at "
        "FROM recommendations WHERE season = ? AND gw = ? "
        "ORDER BY generated_at DESC LIMIT 1",
        [season, gw],
    ).fetchone()
    if rec is None:
        fail("recommendation", f"no recommendation for {season} GW{gw} — run `fplscout optimize`")
        return findings
    squad_json, xi_json, captain, vice, chip, _generated_at = rec
    squad, xi = json.loads(squad_json), json.loads(xi_json)
    squad_set, xi_set = set(squad), set(xi)

    ref = _findings_frame(con, season, gw)
    ref_by_code = ref.set_index("code", drop=False)
    in_ref = squad_set & set(ref_by_code.index)
    missing = squad_set - in_ref
    if missing:
        fail(
            "projections",
            f"{len(missing)} squad players have no reference row: {sorted(missing)}",
        )

    def name(code: int) -> str:
        if code in ref_by_code.index:
            return str(ref_by_code.loc[code, "web_name"])
        return f"code {code}"

    # --- legality --------------------------------------------------------
    if len(squad_set) != SQUAD_SIZE:
        fail("legality", f"squad has {len(squad_set)} players, need {SQUAD_SIZE}")
    if len(xi_set) != XI_SIZE:
        fail("legality", f"starting XI has {len(xi_set)} players, need {XI_SIZE}")
    if not xi_set <= squad_set:
        fail("legality", f"XI players not in squad: {sorted(xi_set - squad_set)}")

    squad_rows = ref_by_code[ref_by_code["code"].isin(squad_set)]
    xi_rows = ref_by_code[ref_by_code["code"].isin(xi_set)]
    pos_counts = squad_rows["position"].value_counts().to_dict()
    for pos, want in SQUAD_COUNTS.items():
        got = pos_counts.get(pos, 0)
        if got != want and not missing:  # counts are meaningless if rows are missing
            fail("legality", f"squad has {got} {pos}, need {want}")
    xi_pos = xi_rows["position"].value_counts().to_dict()
    for pos, (lo, hi) in XI_BOUNDS.items():
        got = xi_pos.get(pos, 0)
        if not missing and not (lo <= got <= hi):
            fail("legality", f"XI has {got} {pos}, need {lo}-{hi}")

    club_counts = squad_rows.groupby("team_id").size()
    over = club_counts[club_counts > 3]
    for team_id, n in over.items():
        team = squad_rows[squad_rows["team_id"] == team_id]["team_short"].iloc[0]
        fail("legality", f"{n} players from {team} — max 3 per club")

    if captain is None or captain not in xi_set:
        fail("legality", f"captain {name(captain)} not in the starting XI")
    if vice is None or vice not in xi_set:
        fail("legality", f"vice-captain {name(vice)} not in the starting XI")
    if captain is not None and captain == vice:
        fail("legality", "captain and vice-captain are the same player")

    # initial-draft budget; in-season squads can legitimately exceed 100.0m
    # via price growth, and their bank accounting lives in squad_state.
    if chip == "wildcard" and not missing:
        total_price = squad_rows["price"].sum()
        if pd.notna(total_price) and total_price > BUDGET_TENTHS:
            fail("budget", f"squad costs {total_price / 10:.1f}m > {BUDGET_TENTHS / 10:.0f}.0m")

    # --- availability ----------------------------------------------------
    for _, row in xi_rows.iterrows():
        status = row.get("status")
        if pd.isna(status) or status == "a":
            continue
        chance = row.get("chance_of_playing_next_round")
        news = f" — {row['news']}" if pd.notna(row.get("news")) and row.get("news") else ""
        if status in HARD_OUT_STATUSES:
            fail("availability", f"{row['web_name']} starts but status={status}{news}")
        elif status == "d":
            level = fail if pd.notna(chance) and chance is not None and chance <= 25 else warn
            level(
                "availability",
                f"{row['web_name']} doubtful "
                f"({int(chance) if pd.notna(chance) else '?'}% chance){news}",
            )

    # --- EV sanity -------------------------------------------------------
    if len(ref) < 300:
        warn("universe", f"only {len(ref)} players in the reference frame — expected 400+")
    xi_ev = xi_rows["ev_points"]
    if xi_ev.isna().any():
        bad = xi_rows[xi_ev.isna()]["web_name"].tolist()
        fail("ev", f"XI players with no EV: {bad}")
    elif len(xi_ev):
        if xi_ev.mean() < 2.0:
            fail("ev", f"mean XI EV {xi_ev.mean():.2f} < 2.0 — degenerate projections")
        if xi_ev.max() < 4.0:
            warn("ev", f"best XI EV is only {xi_ev.max():.2f} — premiums look mispriced")
        if captain in ref_by_code.index:
            cap_rank = int((xi_ev > float(ref_by_code.loc[captain, "ev_points"])).sum())
            if cap_rank > 2:
                warn("ev", f"captain {name(captain)} is only #{cap_rank + 1} in the XI by EV")

    # --- freshness -------------------------------------------------------
    generated = con.execute(
        "SELECT MAX(generated_at) FROM projections WHERE season = ? AND gw = ?",
        [season, gw],
    ).fetchone()[0]
    if generated is not None:
        age_hours = (now - generated.replace(tzinfo=UTC)).total_seconds() / 3600
        if age_hours > STALE_PROJECTION_HOURS:
            warn(
                "freshness",
                f"projections are {age_hours:.0f}h old — run `fplscout refresh` + `report`",
            )

    deadline = con.execute(
        "SELECT deadline_time FROM gameweeks WHERE season = ? AND event = ?",
        [season, gw],
    ).fetchone()
    if deadline and deadline[0] is not None:
        deadline_utc = deadline[0].replace(tzinfo=UTC)
        if deadline_utc < now:
            # demo/replay states legitimately point at played GWs; only a LIVE
            # season recommending for a closed deadline is a real failure
            live = con.execute(
                "SELECT COUNT(*) FROM gameweeks WHERE season = ? AND NOT finished", [season]
            ).fetchone()[0]
            (fail if live else warn)(
                "deadline",
                f"GW{gw} deadline {deadline_utc.isoformat()} is in the past",
            )

    return findings


def render_findings(findings: list[Finding]) -> str:
    if not findings:
        return "PREFLIGHT PASS — squad is legal, available, and fresh."
    lines = [f"[{f.level}] {f.check}: {f.detail}" for f in findings]
    n_fail = sum(1 for f in findings if f.level == "FAIL")
    verdict = (
        "PREFLIGHT FAIL — do NOT trust this recommendation."
        if n_fail
        else "PREFLIGHT PASS (with warnings)."
    )
    return "\n".join([*lines, verdict])


def has_failures(findings: list[Finding]) -> bool:
    return any(f.level == "FAIL" for f in findings)
