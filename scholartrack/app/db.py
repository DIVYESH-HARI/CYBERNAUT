"""SQLite access and SQL-file migrations."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path) -> sqlite3.Connection:
    """Autocommit connection; callers open explicit transactions with BEGIN IMMEDIATE."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.stem in done:
            continue
        sql = sql_file.read_text(encoding="utf-8")
        # Table rebuilds need foreign keys off; the pragma only works outside a transaction.
        rebuild = sql.lstrip().startswith("-- foreign_keys: off")
        if rebuild:
            conn.execute("PRAGMA foreign_keys = OFF")
        record = f"INSERT INTO schema_migrations (version, applied_at) VALUES ('{sql_file.stem}', '{iso(utcnow())}');"
        try:
            conn.executescript("BEGIN;\n" + sql + "\n" + record + "\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if rebuild:
                conn.execute("PRAGMA foreign_keys = ON")
        if rebuild and conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError(f"migration {sql_file.stem} left broken foreign keys")
        applied.append(sql_file.stem)
    return applied


def bump_data_version(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE app_meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'data_version'")
