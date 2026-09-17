"""Historical per-matchday archive from UEFA's own feeds.

Six completed seasons are still served (tourId steps by 10). For each we pull
all 17 matchday player files.

WHAT IS AND IS NOT PER-MATCHDAY -- verified, and the distinction is load-bearing:

  REAL per-matchday:  curGDPts (points scored THAT matchday), value, selPer,
                      selInPer/selOutPer, the fixture played.
                      Proof: one player's 17 curGDPts values sum exactly to his
                      season totPts, and price/ownership move non-monotonically.

  NOT per-matchday:   totPts, gS, assist, minsPlyd, bR, cS -- byte-identical
                      across all 17 files. Each season's files were generated in
                      a single retrospective batch after the season ended.

So this archive supports backtesting a DECISION POLICY (transfers, captaincy,
chip timing) against real per-matchday scoring. It does NOT support training a
component model on per-match features, because those features do not exist here.
Assuming otherwise is the mistake that cost the FPL project months.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

from .feeds import MIN_POLL_SECONDS, SEASONS, SKILL, fetch  # noqa: F401

MATCHDAYS = 17
COLUMNS = ["season", "tour", "matchday", "player_id", "name", "team_id", "team",
           "pos", "value", "md_points", "selected_pct", "status",
           "season_total_points", "season_minutes", "season_recoveries",
           "season_goals", "season_assists", "season_clean_sheets",
           "season_saves", "season_motm"]


def matchday_rows(tour: int, matchday: int) -> list[dict]:
    payload = fetch(f"players/players_{tour}_en_{matchday}.json")
    rows = payload["data"]["value"]["playerList"]
    out = []
    for p in rows:
        skill = int(p.get("skill") or 0)
        out.append({
            "season": SEASONS.get(tour, str(tour)),
            "tour": tour,
            "matchday": matchday,
            "player_id": p.get("id"),
            "name": p.get("pDName"),
            "team_id": p.get("tId"),
            "team": p.get("tName"),
            "pos": SKILL.get(skill, "?"),
            "value": p.get("value"),
            # the one genuinely per-matchday scoring column
            "md_points": p.get("curGDPts"),
            "selected_pct": p.get("selPer"),
            "status": p.get("pStatus") or "",
            # season-final aggregates, identical across matchday files
            "season_total_points": p.get("totPts"),
            "season_minutes": p.get("minsPlyd"),
            "season_recoveries": p.get("bR"),
            "season_goals": p.get("gS"),
            "season_assists": p.get("assist"),
            "season_clean_sheets": p.get("cS"),
            "season_saves": p.get("saves"),
            "season_motm": p.get("mOM"),
        })
    return out


def download(out_path: Path, tours: list[int], *, pause: float = 0.4,
             verbose: bool = True) -> int:
    """Pull every matchday of every listed season into one CSV."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for tour in tours:
            got = 0
            for md in range(1, MATCHDAYS + 1):
                try:
                    rows = matchday_rows(tour, md)
                except Exception as exc:                      # noqa: BLE001
                    if verbose:
                        print(f"  {SEASONS.get(tour, tour)} MD{md}: {exc}")
                    continue
                w.writerows(rows)
                n += len(rows)
                got += 1
                time.sleep(pause)
            if verbose:
                print(f"  {SEASONS.get(tour, tour)} (tour {tour}): "
                      f"{got}/{MATCHDAYS} matchdays")
    return n


def verify(csv_path: Path) -> dict:
    """Confirm the archive has the property the backtest depends on.

    Per-matchday points must vary within a player-season AND sum to his season
    total. If that ever stops holding, the backtest is scoring fiction.
    """
    import collections
    by_player = collections.defaultdict(list)
    totals: dict[tuple, int] = {}
    with csv_path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            key = (r["season"], r["player_id"])
            try:
                by_player[key].append(int(r["md_points"] or 0))
                totals[key] = int(r["season_total_points"] or 0)
            except ValueError:
                continue

    checked = varying = matching = 0
    for key, pts in by_player.items():
        if len(pts) < MATCHDAYS or totals.get(key, 0) <= 0:
            continue
        checked += 1
        if len(set(pts)) > 1:
            varying += 1
        if sum(pts) == totals[key]:
            matching += 1
    return {"player_seasons_checked": checked,
            "with_varying_md_points": varying,
            "md_points_sum_equals_season_total": matching}


def demo():
    """Self-check against ONE live matchday -- cheap, and catches feed drift."""
    rows = matchday_rows(70, 5)          # 2024-25, MD5
    assert len(rows) > 500, len(rows)
    assert {r["pos"] for r in rows} <= {"GK", "DEF", "MID", "FWD", "?"}
    scored = [r for r in rows if (r["md_points"] or 0) not in (0, None, "")]
    assert scored, "no player scored in a completed matchday -- curGDPts moved"
    assert rows[0]["season"] == "2024-25"
    print(f"archive.py self-check OK — {len(rows)} rows, "
          f"{len(scored)} scored in 2024-25 MD5")


if __name__ == "__main__":
    demo()
