# Issue #4 captaincy sweep — 2026-07-13

All runs: 2024-25 replay (train 2021-22..2023-24), Dixon-Coles in-season refit
active (issue #3), transfer_penalty=1.5 / max_hits=1, cold-start features in.
`captain_slot_base_pts` = sum of the effective captain's base points across the
season — the targeted metric for this change (doubling means +1 total pt per
base pt vs. an uncaptained slot).

| w (q90 weight) | total | captain_slot_base_pts | hits |
|---|---|---|---|
| 0.0 | 1681 | 254 | 25 |
| 0.3 | 1734 | 309 | 29 |
| 0.5 | 1793 | 339 | 29 |
| 0.7 | 1717 | 300 | 29 |
| 1.0 | 1822 | 364 | — |

Reads:

1. **The captaincy restructure itself is a big win.** w=0.0 already scored 1681
   vs 1444 for the identical transfer config before issues #3/#4 — that delta
   bundles the DC refit, the switch of the armband objective to CURRENT-GW EV
   (it previously argmaxed horizon-collapsed total_ev), and the vice-captain
   fix (the vice had NO objective term before — solver-arbitrary, and autosub
   promotes the vice when the captain blanks).
2. **q90 blending adds real, targeted value**: captain-slot base points rise
   from 254 (w=0) toward ~340-360 at high w. Trend is up but not cleanly
   monotone (w=0.7 dips) — decision-path noise is present even in the targeted
   metric, so the pure-q90 argmax (w=1.0, best single number) is not trusted.
3. **Chosen: w=0.5.** Within the pre-registered grid, near-best on both
   metrics, keeps a mean anchor against quantile-regressor noise.

## Validation (single run, 2025-26, all changes active)

DC refit + w=0.5 + transfer controls: **1,552 pts, 14 hits, 51 transfers** —
vs 1,543 before issues #3/#4. The +349 tuning-season improvement did NOT
transfer: on held-out data these changes are total-neutral (within the ±60
noise band). Consistent with the project's standing conclusion: the remaining
gap to ~1,900+ is prediction quality, not decision logic. The changes ship on
principle (in-season drift tracking; the armband priced at current-GW value,
which is simply correct economics; vice no longer solver-arbitrary), not on a
claimed points gain. 2025-26 was touched exactly once; no captain-slot
breakdown was extracted to avoid a second touch.
