"""Season-kickoff dress rehearsal: fabricate 2026-27 (zero played GWs) in a
scratch copy of the real DB, then run the full day-one chain offline:
features (synthetic GW1 rows) -> train_production -> projections -> optimize
-> DO THIS sheet. Proves ROADMAP_2627.md section 5 works before it matters.
"""

import json
import shutil
import sys
from pathlib import Path

from fplscout import db, pipeline
from fplscout.decide.optimizer import OptimizerInput, optimize
from fplscout.features.build import write_features
from fplscout.preflight import has_failures, render_findings, run_preflight
from fplscout.report.weekly import render_weekly

SRC = Path(sys.argv[1])
SCRATCH = Path(sys.argv[2])

shutil.copyfile(SRC, SCRATCH)
con = db.connect(SCRATCH)

# the source DB may already carry a provisional/live 2026-27 (e.g. from
# scripts/provisional_2627.py) — the rehearsal fabricates its own; start clean
for table in (
    "teams", "fixtures", "gameweeks", "player_season", "player_gw_history",
    "features", "projections", "recommendations",
):
    con.execute(f"DELETE FROM {table} WHERE season = '2026-27'")

# --- fabricate 2026-27 exactly as live_gw.sync_current_season would leave it ---
con.execute("""
    INSERT INTO teams SELECT '2026-27', team_id, code, name, short_name, strength,
        strength_overall_home, strength_overall_away, strength_attack_home,
        strength_attack_away, strength_defence_home, strength_defence_away
    FROM teams WHERE season = '2025-26'
""")
# full 380-fixture calendar, all unfinished, no scores; difficulties carried over
con.execute("""
    INSERT INTO fixtures
    SELECT '2026-27', fixture_id, event,
           kickoff_time + INTERVAL 364 DAY, team_h, team_a,
           NULL, NULL, team_h_difficulty, team_a_difficulty, false
    FROM fixtures WHERE season = '2025-26'
""")
con.execute("""
    INSERT INTO gameweeks (season, event, deadline_time, finished, average_entry_score)
    SELECT '2026-27', event, deadline_time + INTERVAL 364 DAY, false, NULL
    FROM gameweeks WHERE season = '2025-26'
""")
# player universe with live prices: last known 2025-26 price as stand-in now_cost
con.execute("""
    INSERT INTO player_season (season, element_id, code, team_id, position, web_name, value)
    SELECT '2026-27', ps.element_id, ps.code, ps.team_id, ps.position, ps.web_name,
           (SELECT h.value FROM player_gw_history h
            WHERE h.code = ps.code AND h.season = '2025-26'
            ORDER BY h.gw DESC LIMIT 1)
    FROM player_season ps WHERE ps.season = '2025-26'
""")
con.execute("UPDATE player_season SET value = 45 WHERE season = '2026-27' AND value IS NULL")
assert con.execute(
    "SELECT COUNT(*) FROM player_gw_history WHERE season = '2026-27'"
).fetchone()[0] == 0, "rehearsal premise: zero played rows"

# --- day-one chain ---
n = write_features(con)
synth = con.execute(
    "SELECT COUNT(*) FROM features WHERE season = '2026-27'"
).fetchone()[0]
print(f"features: {n} rows total, {synth} synthetic 2026-27 GW1 rows", flush=True)
assert synth > 0

season, gw = pipeline.latest_reference_point(con)
print(f"reference point: {season} GW{gw}", flush=True)
assert (season, gw) == ("2026-27", 1)

models = pipeline.train_production(con, models_dir=SCRATCH.parent / "models")
proj = pipeline.generate_projections(con, models, season, gw)
finite = proj["ev_points"].notna().sum()
print(f"projections: {len(proj)} players, {finite} with finite EV", flush=True)
assert finite > 500

roster = pipeline.roster_snapshot(con, season, gw)
total_ev = pipeline.total_ev_for_optimizer(con, models, season, gw, proj)
total_ev_df = total_ev.rename("total_ev").reset_index().rename(columns={"index": "code"})
opt_df = roster.merge(total_ev_df, on="code", how="inner")
opt_df = opt_df.merge(
    proj[["code", "ev_points", "q90_points"]], on="code", how="left"
)
opt_df["cap_ev"] = 0.5 * opt_df["ev_points"] + 0.5 * opt_df["q90_points"]
opt_df = opt_df.dropna(subset=["total_ev", "price", "position", "team_id"])
opt_df["price"] = opt_df["price"].astype(int)
print(f"optimizer universe: {len(opt_df)} players", flush=True)

result = optimize(
    OptimizerInput(
        projections=opt_df[["code", "position", "team_id", "price", "total_ev", "cap_ev"]],
        current_squad=set(), purchase_prices={}, bank=1000, free_transfers=1,
        chip_mode="wildcard",
    )
)
assert result.status == "Optimal", result.status
spend = opt_df.set_index("code").loc[list(result.squad), "price"].sum()
print(f"GW1 draft: 15 players, spend {spend/10:.1f}m, captain code {result.captain}",
      flush=True)

# write the recommendation + render the DO THIS sheet, same as `fplscout report`
con.execute(
    "INSERT INTO recommendations (season, gw, generated_at, squad, starting_xi, "
    "captain_code, vice_captain_code, transfers, hits, chip, confidence) "
    "VALUES (?, ?, now(), ?, ?, ?, ?, '[]', 0, 'wildcard', NULL)",
    [season, gw, json.dumps(sorted(result.squad)), json.dumps(sorted(result.starting_xi)),
     result.captain, result.vice_captain],
)
print(render_weekly(con, season, gw))

# the same gate `fplscout publish`/`kickoff` runs — a rehearsal that produces a
# draft preflight would refuse is a failed rehearsal
findings = run_preflight(con, season, gw)
print(render_findings(findings), flush=True)
assert not has_failures(findings), "preflight FAILed the rehearsal draft"
con.close()
print("DRESS REHEARSAL PASSED", flush=True)
