"""Rebuild a local DuckDB from a parquet export (scripts/export_live.py --all).

usage: python scripts/import_parquet.py PARQUET_DIR data/fpl.duckdb
Creates the schema first (db.init_schema) so column types match production,
then inserts every table found in the export.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fplscout import db


def main(src: str, dst: str) -> None:
    con = db.connect(Path(dst))
    db.init_schema(con)
    for f in sorted(Path(src).glob("*.parquet")):
        t = f.stem
        con.execute(f"DELETE FROM {t}")
        con.execute(f"INSERT INTO {t} BY NAME SELECT * FROM read_parquet('{f}')")
        print(f"  {t}: {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]}")
    con.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
