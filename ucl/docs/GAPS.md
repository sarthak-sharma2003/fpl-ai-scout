# Known gaps

Audited 2026-09-09, MD1 in progress. Produced by an adversarial pass
(`scripts/audit.py`) that looks for failures rather than confirmation:
**2 FAIL, 12 GAP, 2 PASS.** Re-audited 2026-09-11 after MD1: **0 FAIL, 12 GAP, 4 PASS** — both P0 FAILs below are fixed.

FAIL = broken right now. GAP = runs, but the behaviour is wrong or absent for a
case that *will* occur this season.

Nothing here is speculative. Every item was reproduced against the live feed.

---

## P0 — broken right now

### 1. The optimizer is infeasible. The next publish will crash. `FIXED 2026-09-11`

```
eligible for XI: {'FWD': 6, 'MID': 4}     # 10 players, of 967
captain-eligible: 0
RuntimeError: infeasible: Infeasible
```

An XI needs 1 GK and 3 DEF. **Zero of either are eligible.** Cause is a
five-step cascade, and every step is individually defensible:

1. **UEFA zeroed every cumulative stat when MD1 locked.** At 16:45 UTC on
   2026-09-08 all 1,163 players went to `minsPlyd=0, totPts=0, gS=0, bR=0`.
   That morning Kane had 1,039 minutes and Pacho 105 recoveries. The
   prior-season cold-start features this project was built on evaporated
   without warning.
2. `estimate_minutes` requires `minutes >= 270` (confidence `0.6 × 450`) before
   it will call a minutes figure `OBSERVED_UCL`. After the reset nobody
   qualifies.
3. So everything falls through to `IMPUTED_PRICE`:
   `price_prior_minutes = (value − 4) / 7 × 90`.
4. That clears the 70-minute XI gate only at **value ≥ €9.4m**.
5. Only premium attackers cost that much. No keeper or defender in the game
   does → no legal XI exists.

`can_captain` additionally requires `minutes.trusted`, so **nothing is
captainable** either.

**This is the same bug fpl-ai-scout already had and documented**
(`gw3-model-distrust`: "XI minutes floor … barred 515/652 on a `p_60_plus`
guess … so no legal XI existed and holding transfers came back Infeasible").
I ported the guard and did not port the lesson. That repo's fix — *exempt
players with a real start this season* — is the fix here too.

**Fix:** make the gate degrade instead of biting. If fewer than N players clear
it, relax it until a legal XI exists, and say so in the output. A guard that can
make the problem unsolvable is worse than no guard.

**Fixed 2026-09-11.** `optimize._feasible_gate` lowers the gate in steps
(70 -> 60 -> 45 -> 30 -> 0) until each position can field its XI minimum, and
reports which threshold it used on `Squad.min_start_used` / `Squad.note`. The
captain guard drops the same way when nothing has observed minutes at all.
Covered by the module self-check: an all-imputed pool still returns a legal XI.

### 2. In-season minutes are ignored for ~3 matchdays. `FIXED 2026-09-11`

184 players already have real 2026/27 minutes (Mbappé 89, Haaland 90,
Vinícius 87). All are still marked `IMPUTED_PRICE`, because the confidence
threshold (270 minutes) was calibrated for a *prior season* of data. In-season
it takes roughly three matchdays before any player counts as observed — during
exactly the window where the model most needs real information.

**Fix:** scale confidence by matchdays played so far, not by an absolute
minutes count.

**Fixed 2026-09-11.** `project.observed_minutes_needed` asks for
`min(270, 0.6 x team_peak_minutes)`, i.e. a share of the sample that actually
exists. A full prior season still needs 270 (behaviour unchanged, so the
backtest is untouched); one matchday in, a 90-minute start counts as observed
and a 12-minute cameo does not. Observed players at MD2: 350 of 966, up from 0.

---

## P1 — wrong by design, will cost points

### 3. The minutes gate measures the wrong quantity

957 of 967 players are currently blocked. Expected minutes are computed as a
player's share of **his club's highest-minutes team-mate**. Kvaratskhelia
played 1,141 minutes for PSG; Pacho played 1,560; so Kvaratskhelia scored
`90 × 1141/1560 = 66` and was barred as a rotation risk — because a centre-back
plays more minutes than an attacker, which is true almost everywhere.

The gate is meant to ask *will he start*. It actually asks *does he play more
minutes than his club's iron man*. **This is why Kvaratskhelia, Raphinha and
Dembélé never reached the MD1 optimiser.**

**Fix:** gate on P(start), not expected minutes. A nailed starter subbed on 70'
must sail through.

### 4. No price drift, no sell-on rule

`plan.py:180` costs **every** matchday at today's price. UCL prices move on
performance at each deadline in €0.1–0.2m steps, so a planned target is dearest
precisely when you most want him — the error is systematically against you, not
random. A plan reading "MD5: sell Álvarez → buy Mbappé" can be quietly
unexecutable by the time MD5 arrives.

Also **unverified: whether UCL taxes profit on a sale** the way FPL does. If it
does, holding a riser is worth more than the model believes.

**Fix, cheap:** stress-test each planned transfer against target +€0.2m/matchday
with sellables flat, and flag steps that become unaffordable.
**Fix, proper:** the archive holds per-matchday `value` *and* points for two
seasons — fit the price-response coefficient empirically.

### 5. Elimination is ingested and then ignored

`Team.eliminated` is parsed and never read by `plan`, `project`, `optimize` or
`pipeline`. The backtest is unambiguous that this is the **single largest
signal available** (+92 points, more than Elo's +33), because from MD9 the field
halves repeatedly and an eliminated club scores zero forever.

The measured edge of this whole project is **entirely in the knockouts**
(+10.7 and +12.6 points per matchday vs the crowd; in the league phase the model
is slightly *worse* than the crowd). The one thing that generates that edge is
not wired in.

**Fixed 2026-10-13.** Three layers, all in the live pipeline:

1. *Out means out.* A club flagged `isEliminated`, or whose players carry
   status `E` (how the completed 2025-26 feed marks all 34 non-finalists),
   projects nothing; `E` is now an unavailable status like `I`/`NIS`/`S`.
   A club with no fixture on a drawn knockout matchday (out, or a top-8 bye
   at MD9-10) is in the plan at **zero**, not dropped -- before, the planner
   silently removed anyone missing from any matchday, which at MD9 would
   have thrown every top-8 player out of the pool for the R16.
2. *Who will still be in.* `knockout.simulate` is a Monte Carlo (2,000 runs,
   <1 s) of the rest of the season on the same Elo and Elo->goals map the
   projections use: Poisson league results to a table, the real 2024+
   bracket seeding into the play-offs and R16, two-legged ties, a neutral
   final. Everything the feed already knows is fact (results, drawn
   pairings, eliminations). It gives P(club plays matchday md) and the mean
   opponent rating when it does. Assumptions are listed in the module.
3. *Weighting.* A knockout matchday not yet drawn projects
   P(plays) x (points vs a generic opponent of that expected rating, neutral
   venue). Published to `teams.json` as `pAlive` per round.

Archive replay (`backtest.Policy`, form-only policy, net points):

| | 2024-25 | 2025-26 | |
|---|---|---|---|
| planner (as before) | 709 | 704 | |
| + per-round rules in the simulator | 774 | 723 | +65 / +19 |
| + **zero a club with no fixture** | **902** | **879** | **+128 / +156** |
| + P(alive)-weighted transfers | 895 | 922 | -7 / +43 |
| + second-leg rotation (#8) | 891 | 908 | -4 / -14 |

The elimination signal replicates (+92 in the old sweep, +128/+156 here) and
is the only step clearly above the ~50-point noise of two seasons. The
P(alive) lookahead is noise-level (+36 over both); it is what makes the
horizon past MD8 meaningful at all, so it stays, but it is not a measured
win. Nothing was tuned on these totals.

### 6. The horizon stops at MD8

Knockout fixtures are undrawn, so the planner sees MD1–8 of 17. Consequences:
both chips look worthless (nothing after MD8 to spend them on), and the wildcard
had to be explicitly barred from the horizon's last matchday to stop the solver
parking it there. The chip-timing answer — the most defensible original edge
this project has, since **no public source gives a numeric EV for either chip** —
cannot be computed until the bracket exists.

**Fixed 2026-10-13.** `pipeline.horizon` runs to MD17: drawn matchdays as
before, undrawn knockout ones P(alive)-weighted (#5). The plan is solved in
stretches split at the free rebuilds (MD2-8 | MD9-10 | MD11-17): unlimited
transfers at MD9 and MD11 make the stretches independent except for the one
wildcard, which `build_plan` prices explicitly -- spending it in the league
phase must beat `max(WILDCARD_HOLD_VALUE, its best knockout gain)`. A full
season plan for the real squad solves in ~6 s.

What it showed, honestly: before the draw the knockout wildcard is worth
**0.0** projected points (from MD2 and from MD8). A P(alive)-weighted generic
projection moves every club's players together round to round, so the free
rebuilds already buy the best squad. The wildcard's knockout value is real but
only becomes visible once a round is drawn; so `WILDCARD_HOLD_VALUE` stays at
10 as the residual for what a deterministic plan cannot see (documented in
`plan.py`, not tuned). Limitless had the same problem in reverse: once it was
computed at all (an injured bench player made every matchday "illegal", so it
was always `None`), the best use for the MD2 squad was +4.2 at MD2. It now
needs `LIMITLESS_HOLD_VALUE` (= 10) to be advised; below that the advice is to
hold it. League-phase advice for MD2 and MD3 is unchanged move for move.

### 7. Per-round rules are not modelled

Both change mid-season and the planner holds one value for the whole horizon:

| | league | KO playoff | R16 | QF | SF | final |
|---|---|---|---|---|---|---|
| max per club | 3 | 4 | 4 | 5 | 6 | 8 |
| budget | €100m | €105m from the knockouts onward | | | | |

A plan spanning the boundary is over-constrained and under-funded late.

**Fixed 2026-10-13.** `knockout` holds the round table (budget, club cap, free
transfers) and `plan_horizon` takes budget and max-per-club per matchday. The
EUR5m rise is added once: to knockout matchdays when the funds were measured in
the league phase, and to the squad file's bank when `advance` crosses MD8->MD9.
Knockout transfer allowances (2 before MD10; 3 before MD12/14/16; 5 before
MD13/15/17; nothing carries) are constants. Secondary sources give "5 before
the QF and the final, 3 before second legs"; the fixtures feed of both
completed seasons agrees and fills the gaps (`subsAllowed` 2/3/5 as above,
`maxSubsCf` 1 in the league phase and 0 in every knockout matchday) -- still
unverified as rules text. The feed's own `maxTeamPlayers` wins for the
matchday it describes.

### 8. Two-legged ties are not modelled

From MD9 each round is two matchdays against the same opponent, and the feed
carries `htAggScore`/`atAggScore`. Nothing uses it. A club 3–0 up rotates the
second leg; a club 3–0 down may throw everything forward. Both are strong,
knowable minutes signals and both are invisible.

**Done 2026-10-13, small on purpose.** `htAggScore`/`atAggScore` cannot be
trusted: populated in 2024-25 second legs, "0"/"0" throughout 2025-26. The
lead is read from the first-leg RESULT instead. Measured on every two-legged
tie of both seasons: of the first-leg regulars at the 13 clubs 3+ up, 27.5%
scored under 2 in the second leg vs 22.4% in close ties (~7% fewer starts),
but their points per regular did not fall (1.19x league average vs
1.13-1.17x). So a club 3+ up gets a 0.93 haircut on minutes, P(start) and
points, and the trailing side is left alone (3+ down was the WORST bucket,
0.81x -- the "throws everything forward" story is not in the data). Replay:
-4 / -14, noise. Kept because the start-rate effect is real and the cost is
small; it is the first thing to drop if it keeps reading negative.

---

## P2 — built but not connected

### 9. Manual subs and the captain switch are reminders, not logic

`matchday_checklist()` prints advice. There is **no code** that picks which
player to sub or who to move the armband to after day one.

This is the largest *behavioural* edge in the research: top-5000 managers switch
captain mid-matchday **44.6%** of the time against **6.1%** for the average
manager, and make manual subs **96.2%** against **15.0%**. MD1 spans three days
(Tue/Wed/Thu), so it applies immediately. The tool tells you to do the thing and
then does not help you do it.

**Captain switch: FIXED 2026-10-13.** `captaincy.plan_captaincy` picks the
pre-deadline armband with the one-way switch priced in (an early-day captain
backed by the best later-day XI player), and publishes the rule
`squad.captainSwitch = {captainId, switchTo, switchIfBelow}` plus a checklist
line ("If he scores 5 or fewer, switch to ..."). Which player to sub is still a
reminder only.

### 10. `rotation_pairs()` is dead code

Written, tested, called by nothing — not `publish`, not the CLI, not the site.
Goalkeeper/defender rotation across alternating fixtures is standard practice
and the function to support it exists and is unreachable.

### 11. Ownership is published but never used

The backtest's clearest result: ownership alone scores **924** where the
form-based model scored **735**. It is written to the site and never enters a
projection. (Caveat honoured: in the backtest it stands in for availability data
the live pipeline has directly, so it must not be ported at that weight — but
zero is also wrong.)

### 12. No world-rank objective

The stated goal is global rank. EV-maximal is also most-owned, which caps you in
the top few percent — you need deliberate variance. There is no
`EV × (1 − ownership)` term, no differential slots, and no risk dial.

### 13. Captaincy is ranked on the mean

Captain is `argmax(mean projection)`. The FPL project measured its mean head
ranking midfielders at ~0.0 correlation while a q90 head hit **+0.6**. No upside
or tail model exists here at all — and captaincy is a doubled slot, so it is the
single highest-leverage projection in the squad.

### 14. The site cannot tell "act now" from "already running"

`publish.py` emits no locked flag, so the page cannot distinguish a matchday you
can still change from one in progress. MD1's three-day span makes this a real
state, not an edge case.

---

## What is actually verified

Not everything is a gap. These were measured, not assumed:

- **Decision policy beats the crowd by +84**, and it replicated exactly
  out-of-sample (tune 2024-25: 924 → 1008; validate 2025-26: 1023 → 1107).
  fpl-ai-scout's comparable change gained +349 tuning and +9 on holdout.
- **Benchmarks that exist nowhere publicly**: crowd template ≈ 924–1023 a
  season (54–60/matchday, matching the 50–65 figure for an average manager);
  perfect-hindsight ceiling ≈ 1390–1472.
- **Per-matchday archive is genuine**: 1,616/1,616 player-seasons vary *and*
  sum exactly to their season total.
- **Domestic Elo makes ratings worse** and is off deliberately, with the reason
  and the real fix recorded.
- Deadline parsing is cross-checked across two feeds that disagree on format
  and carry no timezone.

---

## Suggested order

1. **#1** — it is broken now and the site publishes 3× daily.
2. **#2, #3** — the minutes model is the single largest input and is wrong
   twice over.
3. **#5** — the biggest measured signal, currently unused, and it lands at MD9.
   *(done 2026-10-13)*
4. **#4** — before the first planned transfer is acted on.
5. **#9** — cheap, and the largest behavioural edge in the research.
6. **#6, #7, #8** — when the knockout bracket is drawn. *(done 2026-10-13,
   ahead of the draw; re-check the chip bars once a round is drawn)*
