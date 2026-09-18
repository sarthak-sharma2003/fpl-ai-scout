# P0-A over-trading sweep — 2026-07-12

Grid over `transfer_penalty` (EV charge per transfer, free ones included — banked-FT
option value) × `max_hits` (paid-transfer cap per GW). All runs: 2024-25 replay,
train 2021-22..2023-24, DECISION_HIT_COST=6 unchanged. 2025-26 touched exactly once
at the end (validation, never tuned on).

## Sweep 1 — old feature set (pre cold-start)

| penalty | max_hits | points | hits | transfers |
|---|---|---|---|---|
| 0.0 | — | 1571 | 73 | 110 |
| 0.0 | 1 | 1532 | 31 | 68 |
| 1.0 | — | 1546 | 45 | 82 |
| 1.0 | 1 | 1525 | 28 | 65 |
| 1.5 | 1 | 1578 | 24 | 61 |
| 2.0 | 1 | 1546 | 22 | 59 |

Baseline (0.0, —) reproduces the pre-refactor 1,571 exactly — the prepare/replay
split is faithful.

## Sweep 2 — with prev_season_* cold-start features (P0-B)

| penalty | max_hits | points | hits | transfers |
|---|---|---|---|---|
| 0.0 | — | 1512 | 67 | 104 |
| 0.0 | 1 | 1493 | 33 | 70 |
| 1.0 | — | 1310 | 49 | 86 |
| 1.0 | 1 | 1523 | 30 | 67 |
| 1.5 | 1 | 1444 | 25 | 62 |
| 2.0 | 1 | 1529 | 22 | 59 |

## Honest read

1. **Season totals cannot rank these configs.** The spread within each sweep
   (and the rank flips between sweeps — e.g. (1.5,1) is best in sweep 1, near
   worst in sweep 2) shows single-replay decision-path noise of ±60+ pts: one
   different early transfer cascades through the whole season. The (1.0, —)
   1310 outlier is this effect, not a real property of that config.
2. **The original "over-trading bleeds 100-200 pts" hypothesis is NOT
   confirmed.** Cutting hits from 73 to ~22-30 leaves totals flat within noise —
   the old model's hits were roughly EV-neutral on average, each -4 recouping
   about 4 points.
3. **Chosen config: penalty=1.5, max_hits=1 — on priors, not on the argmax.**
   Rationale: published estimates put a banked FT's option value at ~1-2 pts;
   a 1-hit cap bounds worst-case churn weeks; ~24 hits/season is far closer to
   real strong-manager behavior (5-10) than 73; and fewer model-error-driven
   trades is strictly safer live, where prices/availability add friction the
   backtest doesn't model.
4. Cold-start features (sweep 2's feature set) shifted the baseline 1571→1512 —
   also within noise; the model-level metrics (per-GW Spearman/RMSE, phase3
   report) improved on both splits, which is the instrument that can actually
   measure a feature change.

## Validation (single run, chosen config, new features)

2025-26 (train 2021-22..2024-25), penalty=1.5, max_hits=1, new features:
**1,543 pts, 17 hits, 54 transfers** (old-baseline comparison: 1,601 pts, 65
hits). Within the noise band — the changes are total-neutral on historical
replays. What actually changed: churn down ~4x (65→17 hits), model-level
Spearman/RMSE slightly up, and the 26/27 GW1 draft will see new signings and
last season's form instead of all-NaN features. The remaining 1,550→2,000 gap
is a prediction-quality gap (Spearman ~0.22-0.27), not a decision-layer one —
the live-only levers (availability overlay #1, our own ep_next archive, live
ingestion #2) are where the next real points are.
