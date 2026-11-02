"""SQLite access + forward-only SQL migrations (migrations/NNN_*.sql, tracked in schema_migrations)."""
import sqlite3
from pathlib import Path

from .config import ROOT

MIGRATIONS = ROOT / "migrations"


def connect(path):
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    for f in sorted(MIGRATIONS.glob("*.sql")):
        if f.name in done:
            continue
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations(name) VALUES (?)", (f.name,))
        conn.commit()


def open_db(path):
    conn = connect(path)
    migrate(conn)
    return conn


def ensure_source(conn, source_key, agency, base_url, timezone, refresh_seconds, operator_type="Government"):
    """Idempotent upsert of a source row; keeps run-state columns untouched."""
    conn.execute(
        """INSERT INTO sources (source_key, agency, base_url, timezone, refresh_seconds, operator_type)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(source_key) DO UPDATE SET agency=excluded.agency, base_url=excluded.base_url,
             timezone=excluded.timezone, refresh_seconds=excluded.refresh_seconds, operator_type=excluded.operator_type""",
        (source_key, agency, base_url, timezone, refresh_seconds, operator_type))
    conn.commit()
    return conn.execute("SELECT * FROM sources WHERE source_key=?", (source_key,)).fetchone()
