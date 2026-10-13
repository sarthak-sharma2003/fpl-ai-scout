# uclscout

A UEFA Champions League Fantasy prediction and multi-matchday planning tool.

UEFA's fantasy feeds are public JSON, no auth required:
`https://gaming.uefa.com/en/uclfantasy/services/feeds`. The `tourId` steps by
10 per season (90 = 2026/27).

## Quickstart

```bash
pip install -e ".[dev]"
uclscout --help
```

## Data

Predicted line-ups (`uclscout.lineups`) come from three free scrapes —
RotoWire, Sportsgambler, FutbolFantasy — republished as JSON by
[knapsack-football-formations](https://github.com/pabloroldan98/knapsack-football-formations).
That repo has no licence, so the files are fetched at run time and never
committed here. They feed P(start) for the next open matchday only, and any
source failing just drops that source.

The historical archive (`uclscout.archive`) gives real per-matchday POINTS,
price, and ownership. Season component stats (goals, assists, minutes,
clean sheets, etc.) are only available as season-final totals, not broken out
per matchday -- UEFA generates each season's player files in a single
retrospective batch after the season ends.
