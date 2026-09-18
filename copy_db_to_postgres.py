"""Copies the studio's accounts database (data/cinecut.db) into the Postgres named by DATABASE_URL, for the hosted site.

    python copy_db_to_postgres.py            # shows what would be copied
    python copy_db_to_postgres.py --go       # copies it
    python copy_db_to_postgres.py --go --from other.db

Rows keep their ids, so sessions, work items and agreement records still point at the right accounts. A row whose key
is already in Postgres is left as it is, so running it twice is harmless. The agreement records (consents) are copied
unchanged: they are the evidence of what each person accepted and when.
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.webapp import db  # noqa: E402

TABLES = ["users", "user_country", "sessions", "ledger", "progress", "orders", "subscriptions", "orgs", "members",
          "org_titles", "completions", "api_keys", "api_usage", "work_items", "consents"]
SERIAL = ["users", "ledger", "subscriptions", "orgs", "api_usage", "consents"]


def main() -> None:
    if not db.PG:
        sys.exit("Set DATABASE_URL (in .env) to the Postgres connection string first.")
    go = "--go" in sys.argv
    src_path = Path(sys.argv[sys.argv.index("--from") + 1]) if "--from" in sys.argv else db.DB_PATH
    if not src_path.is_file():
        sys.exit(f"No database at {src_path}.")
    src = sqlite3.connect(str(src_path))
    src.row_factory = sqlite3.Row
    db.init()
    total = 0
    for table in TABLES:
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        total += len(rows)
        print(f"{table:14s} {len(rows):6d} rows")
        if not go or not rows:
            continue
        cols = rows[0].keys()
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) ON CONFLICT DO NOTHING"
        with db.conn() as c:
            for r in rows:
                c.execute(sql, tuple(r))
    if go:
        with db.conn() as c:                 # new rows continue after the copied ids
            for table in SERIAL:
                c.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
        print(f"copied {total} rows into Postgres")
    else:
        print(f"{total} rows would be copied. Run again with --go to copy them.")


if __name__ == "__main__":
    main()
