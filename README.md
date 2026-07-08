# FPL AI Scout

Local-first system that predicts FPL points and optimizes squad/transfer/captain/chip
decisions for one team, 2026/27 season. Full build plan: [`FPL_AI_SCOUT_PLAN.md`](FPL_AI_SCOUT_PLAN.md).

$0 spent on data: free FPL API + free historical data (vaastav/Fantasy-Premier-League)
only. No paid solvers, no paid hosting.

## Build status

| Phase | What | Status |
|---|---|---|
| 0 | Scaffold + FPL API client | ✅ done |
| 1 | Data layer (DuckDB + historical loader) | ✅ done |
| 2 | Feature store | ✅ done |
| 3 | Prediction models (minutes / team goals / points) | ✅ done — beats both baselines, see validation report |
| 4 | MILP optimizer | ⏳ next |
| 5 | Chip planner | not started |
| 6 | Backtest simulator (go/no-go gate) | not started |
| 7 | API + build our own frontend | not started |
| 8 | Automation + weekly ops | not started |
| 9 | Season kickoff checklist | blocked until 26/27 game launches |

## Quickstart

```bash
# macOS only: LightGBM needs the OpenMP runtime, not installed by pip
brew install libomp

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# schema-validate + smoke-test every endpoint against the live API (no DB writes)
fplscout refresh --raw

# populate DuckDB from vaastav historical data (2021-22 .. 2025-26), idempotent
fplscout refresh

# train minutes/team-goals/points models, write a validation report to data/reports/
fplscout train

# run tests (no live network calls — uses recorded fixtures)
pytest -q

# lint
ruff check src/ tests/
```

## Repo layout

```
config/           settings.yaml (team_id, horizon, decay...), rules.yaml (UI rules)
data/             fpl.duckdb + raw/ + models/ + reports/ — all gitignored, all
                   regenerable via `fplscout refresh` then `fplscout train`
src/fplscout/
  db.py           DuckDB connection + schema (players, teams, fixtures, gameweeks,
                   player_gw_history, our_entry/picks/transfers, projections, recommendations)
  ingest/         fpl_api.py (typed client), schemas.py (pydantic models),
                   vaastav.py (historical loader, 2021-22 .. 2025-26)
  features/       feature store (Phase 2)
  models/         minutes / team_goals / points models (Phase 3)
  decide/         optimizer.py, rules_engine.py, chip_planner.py (Phase 4-5)
  backtest/       season replay simulator (Phase 6)
  api/            FastAPI backend (Phase 7)
  report/         weekly "DO THIS" report generator (Phase 8)
  cli.py          fplscout refresh|train|project|optimize|report|serve
frontend/         our React app, original design (Phase 7)
tests/            pytest suite; tests/fixtures/ holds recorded API payloads for offline tests
```

## Design notes

- **Schema drift is fatal, by design.** `ingest/schemas.py` uses strict pydantic
  models (`extra="forbid"`) so any FPL API change — especially the 26/27 season
  reset — breaks ingestion loudly instead of silently dropping fields. If
  `refresh` starts raising `SchemaDriftError`, re-record `tests/fixtures/*.json`
  from the live API and update the model that failed.
- **Caching.** `FplApiClient` caches every response to `data/raw/*.json` with a
  per-endpoint TTL (bootstrap/fixtures/element-summary: 6h, entry: 1h, live: 5min).
  Throttled to ≤1 req/sec with a browser User-Agent, per plan politeness rules.
- **No authenticated endpoints.** The app tracks squad state itself
  (`decide/squad_state.py`, Phase 4) rather than depending on the flaky
  login-walled `my-team` endpoint; it reconciles weekly against the public
  `entry/{id}/event/{gw}/picks/` endpoint instead.
- **Cross-season player identity uses FPL's persistent `code` field**, not fuzzy
  name matching. Every vaastav `players_raw.csv` carries both a per-season numeric
  `id` (resets each season) and a `code` (stable forever — verified Salah/Haaland
  across all 5 loaded seasons). `code` is the primary join key; name-based fuzzy
  matching is only a fallback for rows an exact join can't resolve. See
  `ingest/vaastav.py` module docstring for what else changed vs. the original plan
  sketch after grounding against the real repo (`cleaned_merged_seasons.csv` is
  stale — missing 2024-25/2025-26 — so every season loads from its own per-season
  files instead).
- **2024-25 vaastav data mixes in "Assistant Manager" rows** (element_type 5 —
  e.g. Arteta, Emery — a 24/25 novelty-chip entity, not a squad player) and uses
  an inconsistent goalkeeper label (`"GK"` vs. `"GKP"` in a handful of 2021-22
  rows). Both are filtered/normalized in `ingest/vaastav.py` — caught because the
  Phase 3 by-position validation report initially had a phantom `AM` "position"
  and a near-empty `GKP` bucket.
- **Points model trains one direct per-position regressor** on `total_points`
  rather than the plan's literal 8-component decomposition (attacking/CS/DEFCON/
  bonus/appearance/saves/cards as separate sub-models combined by formula). Same
  signals (team-goals-model output, minutes-model probabilities, rolling DEFCON/
  xG/xA) are fed in as features either way — see `models/points.py` docstring for
  the full reasoning.
- **Validation methodology, after review correction.** The first-pass Phase 3
  report used a single pooled Spearman correlation over every holdout row and an
  unnulled `xP` baseline — both misleading. Pooled Spearman is dominated by "can
  you tell reserves from starters" (~61% of holdout rows are 0-minute players),
  which a naive 5-GW average also gets almost for free; and vaastav's 2025-26
  `xP` column is entirely 0.0 for ~29 of 38 gameweeks (a real upstream data gap),
  which initially made `xP` look far worse than it is. Fixed both: `fpl_xp` is
  nulled per-gameweek where the whole gameweek reads exactly 0 (`models/
  dataset.py::_null_out_corrupted_xp_gameweeks`), and the reported metric is now
  **mean per-GW Spearman on the decision-relevant subset** (plausible starters:
  expected minutes ≥ 45 or P(60+) ≥ 0.5) — the population the optimizer actually
  ranks — with a second comparison restricted to exactly the gameweeks `xP` has
  data for, so the model isn't scored across a harder 38-gameweek spread while
  `xP` is scored on an easier 11-gameweek subset. Net result, GW-matched: our
  model beats `xP` (0.748 vs 0.591 mean per-GW Spearman) and naive (vs. 0.172)
  overall, and beats both in every position including GKP (0.655 vs. `xP` 0.499)
  and FWD (0.768 vs. `xP` 0.639) — the two positions that looked weakest under
  the original, misleading metric. No semi-decomposition needed. Full numbers in
  `data/reports/phase3_validation_*.md` (regenerated by `fplscout train`).

## Weekly ops runbook

Not written yet — lands in Phase 8 once automation exists.
