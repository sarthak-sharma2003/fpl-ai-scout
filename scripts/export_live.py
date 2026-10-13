"""Export the live season's slice of the DuckDB to parquet, for the live-data branch.

The deploy runner's DuckDB is ephemeral and the FPL API is not reachable from
every dev environment. deploy.yml pushes this export (plus the published site
JSON) to the `live-data` branch so any checkout can `git fetch origin
live-data` and work against real current-season data. Historical seasons are
left out: they rebuild from vaastav anywhere.

usage: python scripts/export_live.py data/fpl.duckdb OUT_DIR
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb


def main(db_path: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(db_path, read_only=True)
    season = con.execute("SELECT max(season) FROM fixtures").fetchone()[0]
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY 1").fetchall()]
    for t in tables:
        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [t]).fetchall()}
        where = f"WHERE season = '{season}'" if "season" in cols else ""
        n = con.execute(f"SELECT count(*) FROM {t} {where}").fetchone()[0]
        if n == 0:
            continue
        con.execute(f"COPY (SELECT * FROM {t} {where}) TO '{out / t}.parquet' "
                    "(FORMAT parquet, COMPRESSION zstd)")
        print(f"  {t}: {n} rows")
    (out / "SEASON").write_text(f"{season}\n")


if __name__ == "__main__":
    main(*sys.argv[1:3])
