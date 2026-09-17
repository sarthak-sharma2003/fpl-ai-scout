"""Adversarial audit of ucl-scout. Looks for failures, not confirmation.

Each check prints PASS / FAIL / GAP. GAP = works as written but the behaviour
is wrong or absent for a case that will actually occur this season.
"""
import sys, json, io, contextlib
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/Users/sarthaksharma/Desktop/ucl-scout/src")

from uclscout import feeds, pipeline, plan, project, optimize, strength

R = []
def rec(tag, name, detail=""):
    R.append((tag, name, detail))
    print(f"{tag:5} {name}")
    if detail:
        for line in str(detail).splitlines():
            print(f"        {line}")

print("=" * 72)
ctx = pipeline.load()
c = ctx.constraints
now = datetime.now(timezone.utc)
print(f"MD{c.matchday}  deadline {c.deadline.isoformat()}  now {now.isoformat()}")
print(f"players {len(ctx.players)}  teams {len(ctx.teams)}  moved {len(ctx.moved_clubs)}")
print("=" * 72)

# 1. Are ANY player stats populated?
nz = sum(1 for p in ctx.players if p.minutes > 0)
pts = sum(1 for p in ctx.players if p.total_points > 0)
rec("FAIL" if nz == 0 else "PASS", "player stats present",
    f"{nz}/{len(ctx.players)} have minutes, {pts} have points. "
    f"UEFA zeroed cumulative stats at MD1 lock." if nz == 0 else f"{nz} with minutes")

# 2. What fraction of projections are guesses?
h1 = pipeline.horizon(ctx, upto=c.matchday)
md = h1.get(c.matchday, [])
imp = sum(1 for p in md if not p.minutes.trusted)
rec("FAIL" if imp == len(md) and md else "PASS", "minutes provenance",
    f"{imp}/{len(md)} imputed, {len(md) - imp} observed."
    + (" Nothing is observed -> every pick is price-driven."
       if imp == len(md) and md else ""))

# 3. Does the horizon cover the knockouts?
full = pipeline.horizon(ctx)
rec("GAP" if max(full) <= 8 else "PASS", "horizon covers knockouts",
    f"horizon = MD{min(full)}..MD{max(full)} of {c.last_matchday}. "
    f"Knockout fixtures are undrawn, so MD9-17 are invisible to the planner "
    f"and both chips look worthless there.")

# 4. max_per_club changes by round (3->4->4->5->6->8). Is that modelled?
src = open("/Users/sarthaksharma/Desktop/ucl-scout/src/uclscout/plan.py").read()
per_md_cap = "max_per_club[" in src or "max_per_club.get" in src
rec("PASS" if per_md_cap else "GAP", "per-round max-per-club",
    "planner applies ONE max_per_club to every matchday. Real rule rises "
    "3->4 (KO playoff) ->4 (R16) ->5 (QF) ->6 (SF) ->8 (final). A plan that "
    "spans the boundary is over-constrained late.")

# 5. Budget rises to 105 after the league phase
rec("GAP" if "105" not in src else "PASS", "budget rise after league phase",
    f"constraints says budget={c.budget}; rises to 105.0 for the knockouts. "
    "Planner holds one budget for the whole horizon.")

# 6. Elimination: is isEliminated used anywhere?
elim_used = any("eliminated" in open(f"/Users/sarthaksharma/Desktop/ucl-scout/src/uclscout/{m}.py").read()
                for m in ("plan", "project", "optimize", "pipeline"))
n_elim = sum(1 for t in ctx.teams if t.eliminated)
rec("GAP" if not elim_used else "PASS", "elimination handling",
    f"Team.eliminated is ingested but never read by plan/project/optimize/"
    f"pipeline ({n_elim} eliminated now). Backtest says 'is this club still "
    f"playing' is the single largest signal (+92). It is not wired in.")

# 7. Two-legged ties from MD9
rec("GAP", "two-legged ties",
    "Match carries htAggScore/atAggScore in the feed; nothing models a tie "
    "across two matchdays. A club 3-0 down may rotate the second leg.")

# 8. Manual subs / captain switch: logic or just a reminder?
has_sub_logic = "def " in src and ("subs_allowed" in src and "def plan_subs" in src)
rec("GAP" if not has_sub_logic else "PASS", "manual sub & captain-switch logic",
    "matchday_checklist() prints reminders. There is NO logic that picks WHICH "
    "player to sub or WHO to move the armband to after day 1. Research says "
    "top-5000 do this 44.6%/96.2% of the time vs 6.1%/15.0% average -- the "
    "largest behavioural edge found, and it is unimplemented.")

# 9. rotation_pairs: built but wired in?
wired = "rotation_pairs" in open("/Users/sarthaksharma/Desktop/ucl-scout/src/uclscout/publish.py").read()
rec("GAP" if not wired else "PASS", "rotation pairs surfaced",
    "plan.rotation_pairs() exists and is tested but is called by nothing -- "
    "not by publish, not by the CLI, not by the site.")

# 10. Price drift
rec("GAP", "price drift / sell-on rule",
    "plan.py:180 costs every matchday at TODAY's price. Prices move on "
    "performance each deadline (EUR0.1-0.2m), so a planned buy is dearest "
    "exactly when you want it. No sell-price rule; unknown whether UCL taxes "
    "profit on a sale.")

# 11. Deadline passed - does publish warn?
passed = c.deadline < now
pub = open("/Users/sarthaksharma/Desktop/ucl-scout/src/uclscout/publish.py").read()
rec("GAP" if "locked" not in pub.lower() else "PASS", "locked-matchday awareness",
    f"deadline in {(c.deadline-now).total_seconds()/3600:.1f}h. publish.py emits "
    "no 'locked' flag, so the site cannot tell 'you can still act' from "
    "'this matchday is already running'.")

# 12. Does the optimizer still produce a legal squad with zeroed stats?
try:
    s = optimize.optimize(md, budget=c.budget, max_per_club=c.max_per_club,
                          min_start=70.0)
    legal = (len(s.picks) == 15 and s.cost <= c.budget + 1e-6)
    rec("PASS" if legal else "FAIL", "optimizer still returns a legal squad",
        f"cost EUR{s.cost:.1f}m, captain {s.captain.player.name}, "
        f"proj {s.total_points:.1f}")
except Exception as e:
    rec("FAIL", "optimizer with zeroed stats", f"{type(e).__name__}: {e}")

# 13. Minutes gate: does it block nailed starters?
blocked = [p for p in md if p.minutes.expected < 70]
rec("GAP", "minutes gate measures the wrong thing",
    f"{len(blocked)}/{len(md)} blocked from the XI. The gate uses EXPECTED "
    "MINUTES, computed as the player's share of his club's highest-minutes "
    "team-mate. A nailed attacker subbed on 70' looks like a rotation risk "
    "because a centre-back played every minute. Should gate on P(start).")

# 14. Ownership: proven in backtest, used live?
own_used = "selected_pct" in open("/Users/sarthaksharma/Desktop/ucl-scout/src/uclscout/project.py").read()
rec("GAP" if not own_used else "PASS", "ownership as a signal",
    "Backtest: ownership alone scored 924 vs my form model's 735. It is "
    "published to the site but never enters the projection.")

# 15. Differentials / world-rank objective
diff = "ownership" in src.lower() or "differential" in src.lower()
rec("GAP" if not diff else "PASS", "world-rank differential objective",
    "User's goal is global rank. EV-maximal == most-owned, which caps you in "
    "the top few percent. No EV x (1-ownership) term, no risk dial.")

# 16. Captaincy on skew
skew = "q90" in src or "quantile" in src or "ceiling" in src
rec("GAP" if not skew else "PASS", "captaincy ranked on upside",
    "Captain is argmax of mean projection. FPL project learned its mean head "
    "ranked ~0.0 while a q90 head hit +0.6. No upside/tail model here at all.")

print("=" * 72)
for t in ("FAIL", "GAP", "PASS"):
    n = sum(1 for x in R if x[0] == t)
    print(f"{t}: {n}")
