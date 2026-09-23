# Human-style strategy sweep — 2026-09-23

Eleven strategies layered on the production backtest (`backtest/strategies.py`),
three replay seasons, each season's models trained only on earlier seasons.
Re-run: `python scripts/strategy_sweep.py && python scripts/strategy_sweep.py --report`.

| strategy | 2023-24 | 2024-25 | 2025-26 | mean | vs base /GW (±1 se) | hits |
|---|---|---|---|---|---|---|---|
| started + template | 1914.0 | 1968.0 | 2007.0 | 1963 | +1.18 (±1.24) | 35/31/28 |
| nailed3 + template | 1902.0 | 2036.0 | 1895.0 | 1944 | +0.69 (±1.64) | 31/31/29 |
| started last game | 1957.0 | 1949.0 | 1883.0 | 1930 | +0.31 (±1.28) | 35/30/33 |
| premium captain | 2028.0 | 1957.0 | 1780.0 | 1922 | +0.10 (±0.44) | 33/29/31 |
| started + premium captain | 1986.0 | 1945.0 | 1834.0 | 1922 | +0.10 (±1.35) | 35/30/33 |
| baseline (model) | 1972.0 | 1943.0 | 1839.0 | 1918 | +0.00 (±0.00) | 33/29/31 |
| template top-150 | 1973.0 | 1932.0 | 1796.0 | 1900 | -0.46 (±1.25) | 32/28/30 |
| form + started | 1946.0 | 1885.0 | 1805.0 | 1879 | -1.04 (±1.88) | 37/37/36 |
| nailed: 60+ mins x3 | 1930.0 | 1899.0 | 1787.0 | 1872 | -1.21 (±1.37) | 32/32/32 |
| momentum (net transfers in) | 1872.0 | 1810.0 | 1889.0 | 1857 | -1.61 (±1.34) | 35/29/24 |
| form chaser (no model) | 1971.0 | 1877.0 | 1705.0 | 1851 | -1.76 (±1.93) | 36/37/37 |

"vs base /GW" is the paired per-gameweek difference from baseline over all 114
GWs. Its ±se understates the real uncertainty (squads are path-dependent, so
weeks aren't independent). The season noise floor from earlier sweeps is ±130.

## Read

- **Nothing clears the noise.** The best, started-last-game + template top-150,
  is +45/season (+1.2 ± 1.2 per GW) and loses 2023-24 by 58. Not shipped.
- **The model beats naive form by ~67/season** (form chaser 1851 vs 1918), which
  is the one directional result worth knowing, though still inside the floor.
- **Nailed 60+ mins x3 loses all three seasons** (-42, -44, -52): too strict,
  it shuts out new signings and returners the model already prices for minutes.
- **Premium captain ≈ the model's captain** (+0.1 ± 0.4 per GW): the model is
  already mostly captaining the most expensive player.
- Momentum (buy only net-transferred-in players) loses 2 of 3.
