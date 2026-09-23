# Human-style strategy sweep — 2026-09-23

19 strategies layered on the production backtest (`backtest/strategies.py`).
Three replay seasons, each trained only on earlier seasons, plus 2026-27 GW1-5
with real results (shown, never pooled: 5 GWs can't rank anything).
Re-run: `python scripts/strategy_sweep.py && python scripts/strategy_sweep.py --report`.

| strategy | 2023-24 | 2024-25 | 2025-26 | 2026-27 GW1-5 | mean (hist) | vs base /GW (±1 se) | seasons won |
|---|---|---|---|---|---|---|---|
| started + template + xGI | 2076 | 2091 | 1910 | 306 | 2026 | +2.83 (±1.54) | 3/3 |
| started + template | 1914 | 1968 | 2007 | 306 | 1963 | +1.18 (±1.24) | 2/3 |
| started + template + home captain | 1909 | 1952 | 1988 | 296 | 1950 | +0.83 (±1.27) | 2/3 |
| nailed3 + template | 1902 | 2036 | 1895 | 277 | 1944 | +0.69 (±1.64) | 2/3 |
| started last game | 1957 | 1949 | 1883 | 306 | 1930 | +0.31 (±1.28) | 2/3 |
| attackers top-60 by xGI | 1968 | 1922 | 1889 | 305 | 1926 | +0.22 (±1.33) | 1/3 |
| premium captain | 2028 | 1957 | 1780 | 301 | 1922 | +0.10 (±0.44) | 2/3 |
| started + premium captain | 1986 | 1945 | 1834 | 298 | 1922 | +0.10 (±1.35) | 2/3 |
| baseline (model) | 1972 | 1943 | 1839 | 305 | 1918 | +0.00 (±0.00) | 0/3 |
| attackers from top-6 scoring clubs | 1977 | 1995 | 1747 | 310 | 1906 | -0.31 (±1.41) | 2/3 |
| home captain | 1945 | 1919 | 1841 | 295 | 1902 | -0.43 (±0.42) | 1/3 |
| template top-150 | 1973 | 1932 | 1796 | 305 | 1900 | -0.46 (±1.25) | 1/3 |
| form captain | 1936 | 1934 | 1806 | 290 | 1892 | -0.68 (±0.57) | 0/3 |
| form + started | 1946 | 1885 | 1805 | 250 | 1879 | -1.04 (±1.88) | 0/3 |
| nailed: 60+ mins x3 | 1930 | 1899 | 1787 | 276 | 1872 | -1.21 (±1.37) | 0/3 |
| differential (skip top-20 owned) | 1894 | 1877 | 1801 | 295 | 1857 | -1.60 (±1.48) | 0/3 |
| momentum (net transfers in) | 1872 | 1810 | 1889 | 301 | 1857 | -1.61 (±1.34) | 1/3 |
| started + top-6 attack | 1910 | 1872 | 1780 | 309 | 1854 | -1.68 (±1.61) | 0/3 |
| form chaser (no model) | 1971 | 1877 | 1705 | 250 | 1851 | -1.76 (±1.93) | 0/3 |

Your real team, 2026-27 GW1-5: 322 (net of hits)

"vs base /GW" is the paired per-gameweek difference from baseline. Its ±se
understates the real uncertainty (squads are path-dependent). Season noise
floor from earlier sweeps: ±130.

## The one candidate, and why it is NOT shipped

started + template + xGI won all three seasons (+104/+148/+71, +108 mean).
But it was the best of 18 tries, and +2.8 ± 1.5/GW is what the max of 18 null
strategies looks like. Robustness check (template N / xGI N):

| config | 2022-23 (unseen) | 23-24 | 24-25 | 25-26 | mean 23-26 vs base |
|---|---|---|---|---|---|
| baseline | 1769 | 1972 | 1943 | 1839 | +0 |
| **150/60 (the winner)** | 1740 | 2076 | 2091 | 1910 | **+108** |
| 150/40 | 1774 | 1987 | 1840 | 1932 | +2 |
| 150/80 | 1740 | 1944 | 1985 | 1946 | +40 |
| 100/60 | 1739 | 1985 | 1874 | 1930 | +12 |
| 200/60 | 1763 | 2082 | 1955 | 1998 | +94 |

Neighbours collapse to +2..+40 and the unseen season loses by 29: a tuning
spike, the same shape as the rejected q90 horizon blend. Every neighbour is
>= baseline, so the family may be mildly positive (~+50), well inside noise.

## Other reads

- The model beats naive form chasing by ~67/season and form captaincy by ~26.
- Differential (skip the 20 most-owned) loses all three: template players
  are template because they score.
- Nailed 60+ mins x3 loses all three: too strict, shuts out new signings.
- Premium captain ~= model captain; home-only captain is slightly worse.

## 2026-27 GW1-5 (real results)

Your team 322 vs simulated model 305. The whole gap is GW1 (57 vs 38); over
GW2-5 the model is +2 (267 vs 265) despite 4 hits to your 2. One GW is
one captain blank away from noise, so this says little yet.
