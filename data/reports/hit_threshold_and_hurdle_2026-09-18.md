# Hit threshold + hurdle-model experiments — 2026-09-18

Two hypotheses about "the model isn't winning enough", both tested, **both
rejected**. Recorded so neither gets re-proposed from first principles.

## 1. Hit threshold — REJECTED

**Hypothesis.** The backtest takes ~30 hits/season (-120 pts of guaranteed
cost). Raising the decision bar should recover most of that.

Sweep of `DECISION_HIT_COST` (now a `simulate_season` parameter, mirroring
`transfer_penalty`/`max_hits`), both replay seasons, everything else at
production values. `hit_cost=1000` is the decisive arm: it cannot take a hit
at all, so it prices the entire policy rather than tuning a knob.

| hit_cost | 2024-25 pts | hits | 2025-26 pts | hits | mean |
|---|---|---|---|---|---|
| 4 | 1761 | 33 | 1716 | 35 | 1738 |
| **6 (current)** | 1792 | 30 | 1775 | 29 | **1784** |
| 8 | 1887 | 24 | 1664 | 23 | 1776 |
| 10 | 1659 | 11 | 1638 | 7 | 1649 |
| 12 | 1829 | 7 | 1539 | 5 | 1684 |
| 1000 (no hits) | 1874 | 0 | 1702 | 0 | 1788 |

**Read.** Never taking a hit scores 1788 mean vs the current 1784 — a 4-point
difference against a ±130 noise floor. The hits are approximately break-even:
they cost ~120 pts and buy back ~120 pts of EV. The original "-120 pts of pure
waste" framing was wrong, because it counted the cost and not the return.

The per-season columns disagree violently — `hit_cost=8` is the best 2024-25
config (1887) and near the worst in 2025-26 (1664) — and the response is
non-monotonic (10 scores below both 8 and 12 in 2024-25). A 228-point spread
within one season from a knob that should be smooth is decision-path chaos, not
signal. This independently reproduces the P0-A sweep's conclusion
(`p0a_sweep.md`): season totals cannot rank these configs.

**Action: none.** `DECISION_HIT_COST = 6` stays, still chosen on priors. The
parameterization is kept so the next person can re-run this in one command
instead of re-deriving the question.

## 2. Hurdle factorization of the points mean head — REJECTED

**Hypothesis.** The minutes family (`mins_p0`, `mins_p1_59`, `mins_p60_plus`,
`expected_minutes`) takes **54-66% of total gain** in the production mean model
(MID worst at 66%, `expected_minutes` alone 55%) — measured, not guessed, via
`points.feature_gain_by_column`. `minutes.py` already answers "will he play"
explicitly, so the points head is spending most of its capacity re-deriving it
instead of learning "how good is he when he plays" — the only thing that
separates the likely-starter population the optimizer ranks among.

Variant: train the mean head only on rows the player actually played, then
compose `E[pts] = P(play) * E[pts | played]`. Quantile heads untouched (one
variable at a time; captaincy is separately tuned). Judged on mean-per-GW
Spearman over the decision-relevant subset (~9,000 rows/split) — far lower
noise than a season replay.

| split | base Spearman | hurdle Spearman | base RMSE | hurdle RMSE |
|---|---|---|---|---|
| primary (2024-25) | 0.2722 | 0.2792 | 3.0028 | 2.9828 |
| secondary (2025-26) | 0.2241 | 0.2229 | 3.1023 | 3.0929 |

**Read.** RMSE improves on both splits but by ~0.02 — a rounding error. The
decision metric, Spearman, flips sign between splits (+0.007, -0.001). The
theory is not dead: MID, the most minutes-dominated position, is the one
position that improves on *both* splits (+0.011, +0.007), which is exactly what
the diagnosis predicts. But the headline effect is inside noise and does not
justify changing the production model.

**Action: none.** Worth revisiting only alongside a real change to what the
conditional head can learn (e.g. separate heads per minutes bucket), not as a
reparameterization of the same features.

## What did land

Neither experiment, but the investigation that framed them found a real gap:
`projections.json` published only single-gameweek `ev_points`, so the site
ranked every visitor's transfers on it, while our own recommendation has always
used `total_ev_for_optimizer`'s decay-summed 8-GW horizon. Fixed in `34854db`
(rebased to `dd4fa2b`) — the two rankings disagree at Spearman 0.86.
