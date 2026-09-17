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

### 6. The horizon stops at MD8

Knockout fixtures are undrawn, so the planner sees MD1–8 of 17. Consequences:
both chips look worthless (nothing after MD8 to spend them on), and the wildcard
had to be explicitly barred from the horizon's last matchday to stop the solver
parking it there. The chip-timing answer — the most defensible original edge
this project has, since **no public source gives a numeric EV for either chip** —
cannot be computed until the bracket exists.

### 7. Per-round rules are not modelled

Both change mid-season and the planner holds one value for the whole horizon:

| | league | KO playoff | R16 | QF | SF | final |
|---|---|---|---|---|---|---|
| max per club | 3 | 4 | 4 | 5 | 6 | 8 |
| budget | €100m | €105m from the knockouts onward | | | | |

A plan spanning the boundary is over-constrained and under-funded late.

### 8. Two-legged ties are not modelled

From MD9 each round is two matchdays against the same opponent, and the feed
carries `htAggScore`/`atAggScore`. Nothing uses it. A club 3–0 up rotates the
second leg; a club 3–0 down may throw everything forward. Both are strong,
knowable minutes signals and both are invisible.

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
4. **#4** — before the first planned transfer is acted on.
5. **#9** — cheap, and the largest behavioural edge in the research.
6. **#6, #7, #8** — when the knockout bracket is drawn.
