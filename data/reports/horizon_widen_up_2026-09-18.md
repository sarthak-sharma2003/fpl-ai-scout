# Horizon widening promoted fringe players — 2026-09-18

## The bug

`models/horizon.py` blends every player's minutes distribution toward the
position's STARTER average as the horizon extends:

```python
widen = min(1.0, h * UNCERTAINTY_WIDEN_PER_STEP)   # fully at h>=6
widened_proba = (1 - widen) * own_proba + widen * pos_avg
```

That blend is symmetric in form but **not in effect**. For a nailed starter it
is roughly a no-op (they already sit near the starter average) or a small,
correct nudge down — "he might be rotated". For a player below the starter
average it is a *promotion*: by the far end of the horizon a 42%-to-start
rotation risk is modelled as a nailed starter.

The points head then applies that player's per-90 rate to the invented minutes
— and the per-90 rate of a fringe player is flattering *precisely because* it
comes from a small cameo sample. The two errors compound.

This is the overcorrection of the GW4 fix (`gw4-horizon-widening-collapse`),
which changed the regression target from "all players" to "starters" to stop
nailed starters being dragged to ~30 minutes. That fixed the collapse and
opened this.

## Scale of the distortion (live data, 26/27 GW5)

Uplift ratio = `horizon_ev / (ev_points * 4.70)`, where 4.70 is the decay sum —
i.e. how the horizon treats a player versus simply repeating this gameweek.

| population | n | mean uplift |
|---|---|---|
| sub-starters (p60 < 0.5) | 135 | **2.15x** |
| nailed (p60 >= 0.75) | 115 | **0.93x** |

A 2.3x systematic tilt toward fringe players. Worst individual cases: Simms
(44% to appear, ev 0.52 -> horizon 12.19), Gyokeres (55%, 0.82 -> 13.49),
Wilson (6% to appear, NEGATIVE single-gw ev -> positive horizon).

Checked and NOT a bug: Foden shows 0.0 expected minutes and horizon 10.99, but
his news reads "Suspended until 17 Oct" and `availability_return_gw` correctly
holds him out and restores him mid-horizon. The availability fade was not
changed.

## Measurement

Neither existing instrument sees this. `fplscout train`'s holdout Spearman
scores SINGLE-gameweek predictions and is blind to the horizon; the season
backtest exercises it but carries a +/-130 noise floor.

So the horizon was scored **directly**: at every decision gameweek, rank players
by horizon EV against their ACTUAL decay-weighted points over gws g..g+7. Mean
per-decision-gw Spearman, ~10,700 (gw, player) pairs per split. Population
filter uses the UNWIDENED minutes estimate (>= 30 expected minutes), identical
across variants, so it cannot favour one.

`widen_up` scales the widening for players below the starter average only:
1.0 reproduces the old behavior, 0.0 never widens upward.

| widen_up | primary (2024-25) | secondary (2025-26) |
|---|---|---|
| 1.0 (old) | 0.2714 | 0.2228 |
| 0.5 | 0.3368 | 0.2888 |
| **0.0 (shipped)** | **0.3953** | **0.3504** |

Monotonic on both splits, +0.124 and +0.128 — a 46%/57% relative improvement,
and the two independent seasons agree to within 0.004. This is not a tuning
spike: the response is monotone and the effect is two orders of magnitude
larger than the changes rejected the same day
(`hit_threshold_and_hurdle_2026-09-18.md`).

Conservative note: players with no appearance in the window are dropped rather
than scored as 0, which EXCLUDES the most damaging over-promotions. The
measured gain is therefore a floor.

## Shipped

`WIDEN_UP = 0.0`. Every caller inherits it through the parameter default, so
the live pipeline (`pipeline.live_horizon_ev` -> the optimizer and the site's
published `horizon_ev`) and the backtest all move together.

## Before/after on live data (26/27 GW4 snapshot, identical DB and models)

Demoted — players who essentially never play, previously ranked in the top
quartile of transfer targets:

| player | p60 | old horizon (rank) | new horizon (rank) |
|---|---|---|---|
| Anselmino (DEF) | 0.005 | 14.15 (122) | 0.19 (552) |
| Palestra (DEF) | 0.000 | 13.82 (132) | 0.52 (412) |
| Steele (GKP) | 0.002 | 9.96 (325) | 0.11 (631) |

Promoted — nailed starters whose EV did NOT change at all. They rise purely
because the phantom competition fell away, which is the real cost of the bug:
genuine options were buried under fringe players with invented minutes.

| player | p60 | old horizon (rank) | new horizon (rank) |
|---|---|---|---|
| Gakpo (MID) | 0.674 | 6.17 (540) | 6.17 (**270**) |
| Ajayi (DEF) | 0.879 | 8.10 (449) | 8.10 (**241**) |
| van Ewijk (DEF) | 0.867 | 7.91 (464) | 7.91 (**244**) |

The case that started this: **Dorgu** 12.21 (rank 207) -> 6.18 (rank 269);
**Gross** 15.09 (rank 98) -> 15.09 (rank **74**). The fix reverses the call —
the nailed penalty-taker now outranks the rotation risk.

## End-to-end backtest gate

Not the primary evidence (the +/-130 noise floor cannot resolve this), but it
confirms direction and rules out a structural break:

| season | baseline | with fix | delta |
|---|---|---|---|
| 2024-25 | 1792 | **1943** | +151 |
| 2025-26 | 1775 | **1839** | +64 |

Both positive; the primary clears the noise floor on its own. 2024-25 now also
clears the real 25/26 average manager (1895). Hit and transfer counts barely
move (30->29, 29->31), so the gain is better PLAYER SELECTION, not a change in
trading behaviour.
