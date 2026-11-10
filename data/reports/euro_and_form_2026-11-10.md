# European congestion and recent-form weighting: measured, not shipped (2026-11-10)

Two ideas, both asked for in plain words by the manager: "are the midweek
European games considered?" and "it picks someone with fewer points lately but
more minutes". Both were tested on the production model (v2: hurdle + xPts
target, `scripts/model_lab.py` variant `prod`), same rows for every variant.

## What was tested

- **`prod+euro`**: two new inputs to the minutes and points models,
  `euro_days_before` / `euro_days_after` (days from a league kickoff to the
  club's nearest UCL/UEL/UECL tie either side, NaN beyond 8 days;
  `features.add_euro_gaps`). Six seasons of ties, 493 club-fixtures, loaded
  from ESPN (`ingest/european.py`).
- **`prod+formNN`**: `EV = (1 - w) * model + w * roll5_points`, w = 0.1/0.2/0.3.

## Results (seed 0 / seed 1)

| holdout | variant | topk | cap1 | sp_reg | rmse |
|---|---|---|---|---|---|
| 2023-24 | prod | 4.570 / 4.670 | 6.34 / 5.95 | .4117 / .4141 | 1.881 / 1.879 |
| | prod+euro | 4.584 / 4.465 | 6.53 / 6.21 | .4163 / .4162 | 1.880 / 1.881 |
| | prod+form10 | 4.533 / 4.562 | 6.55 / 6.08 | .4187 / .4207 | 1.878 / 1.876 |
| | prod+form20 | 4.434 / 4.534 | 6.68 / 6.55 | .4194 / .4213 | 1.880 / 1.878 |
| | prod+form30 | 4.373 / 4.483 | 7.24 / 6.55 | .4139 / .4146 | 1.887 / 1.885 |
| 2024-25 | prod | 4.661 / 4.657 | 8.03 / 7.92 | .3919 / .3938 | 1.885 / 1.884 |
| | prod+euro | 4.585 / 4.647 | 7.95 / 8.26 | .3940 / .3976 | 1.884 / 1.882 |
| | prod+form10 | 4.746 / 4.664 | 9.34 / 9.37 | .3966 / .3986 | 1.881 / 1.879 |
| | prod+form20 | 4.657 / 4.689 | 9.29 / 9.63 | .3963 / .3982 | 1.882 / 1.881 |
| | prod+form30 | 4.645 / 4.644 | 9.50 / 9.29 | .3892 / .3911 | 1.888 / 1.887 |

Unseen seasons, touched once (seed 0):

| holdout | variant | topk | cap1 | cap3 | sp_reg |
|---|---|---|---|---|---|
| 2025-26 | prod | 4.567 | 5.32 | 5.14 | .3354 |
| | prod+form10 | 4.557 | 4.87 | 5.42 | .3409 |
| | prod+form20 | 4.541 | 5.24 | 5.24 | .3410 |
| 2026-27 GW1-10 | prod | 4.844 | 7.70 | 6.53 | .3473 |
| | prod+form10 | 4.919 | 8.50 | 6.87 | .3550 |
| | prod+form20 | 5.019 | 6.40 | 5.27 | .3473 |

## Verdicts

- **European congestion: REJECTED as a model feature.** Rank correlation moves
  +0.002 to +0.005, top-K moves -0.21 to +0.01 and flips sign between seeds.
  That is noise. The data is still ingested in full (and Brighton's ties are
  no longer dropped), and the site still shows the midweek note per player.
  Not tested: per-PLAYER European minutes (who actually played 90 on
  Thursday). That needs ESPN box scores matched to FPL players by name and is
  the only version of this idea with a plausible signal left.
- **Form weighting: REJECTED.** The captain pick improved in both tuning
  seasons at every weight (cap1 +0.1 to +1.7), which looked like a finding;
  on 2025-26 it went the other way (5.32 -> 4.87 at w=0.1) and on 2026-27
  w=0.1 and w=0.2 disagree in sign. A tuning-season pattern that does not
  survive an unseen season, the same shape as the rejected q90 horizon blend.
  What IS consistent: w=0.1 lifts rank correlation by 0.005-0.008 in all four
  seasons and both seeds. Real, and too small to change a pick.

## What this session's audit found instead

The "obviously wrong" calls came from the decision layer, not the points model:
the lineup was picked on the 8-week sum, every chip sat behind a bar sized for
double gameweeks, and training was not reproducible (unordered rows into a
seeded bagger: 0.61 EV of pure noise on a player, different transfers every
deploy). All three are fixed and live. Note the old `base` variant in the lab
is the pre-October direct regressor; form blending DOES help that one, which is
why the first pass looked promising. Compare against `prod`.
