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
| 3 | Prediction models (minutes / team goals / points) | ⏳ next — validation report is a review gate |
| 4 | MILP optimizer | not started |
| 5 | Chip planner | not started |
| 6 | Backtest simulator (go/no-go gate) | not started |
| 7 | API + build our own frontend | not started |
| 8 | Automation + weekly ops | not started |
| 9 | Season kickoff checklist | blocked until 26/27 game launches |

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# schema-validate + smoke-test every endpoint against the live API (no DB writes)
fplscout refresh --raw

# populate DuckDB from vaastav historical data (2021-22 .. 2025-26), idempotent
fplscout refresh

# run tests (no live network calls — uses recorded fixtures)
pytest -q

# lint
ruff check src/ tests/
```

## Repo layout

```
config/           settings.yaml (team_id, horizon, decay...), rules.yaml (UI rules)
data/             fpl.duckdb (gitignored) + raw/ JSON + CSV cache (gitignored)
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

## Weekly ops runbook

Not written yet — lands in Phase 8 once automation exists.
