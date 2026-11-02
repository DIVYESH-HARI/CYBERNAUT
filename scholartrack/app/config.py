"""Runtime configuration. Secrets come from the environment or an untracked file, never from code."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("SCHOLARTRACK_DB", ROOT / "data" / "scholartrack.db"))
BASE_URL = os.environ.get("SCHOLARTRACK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SOURCES_FILE = Path(os.environ.get("SCHOLARTRACK_SOURCES", ROOT / "sources.json"))

SCHEDULER_ENABLED = os.environ.get("SCHOLARTRACK_SCHEDULER", "1") == "1"
# Demo mode adds the fictional built-in sources and the mentor-challenge controls. Off = real sources only.
DEMO_MODE = os.environ.get("SCHOLARTRACK_DEMO", "0") == "1"
# Load the bundled snapshot (seed/snapshot.json) for sources that have no data yet, e.g. first start offline.
USE_SNAPSHOT = os.environ.get("SCHOLARTRACK_SNAPSHOT", "1") == "1"

# Each source has its own refresh interval (sources.json "refresh_minutes"). A snapshot older than
# 3 x that interval is shown as stale, never as live. SCHOLARTRACK_STALE_MIN overrides it for every source.
STALE_AFTER_MIN = int(os.environ["SCHOLARTRACK_STALE_MIN"]) if os.environ.get("SCHOLARTRACK_STALE_MIN") else None


def stale_after(refresh_minutes: int) -> int:
    return STALE_AFTER_MIN if STALE_AFTER_MIN is not None else 3 * refresh_minutes

USER_AGENT = os.environ.get(
    "SCHOLARTRACK_USER_AGENT",
    "ScholarTrackBot/1.0 (student project; contact: set SCHOLARTRACK_USER_AGENT)",
)
FETCH_TIMEOUT = float(os.environ.get("SCHOLARTRACK_TIMEOUT", "20"))
FETCH_MAX_RETRIES = int(os.environ.get("SCHOLARTRACK_RETRIES", "3"))
FETCH_BACKOFF = float(os.environ.get("SCHOLARTRACK_BACKOFF", "1.0"))
FETCH_MIN_INTERVAL = float(os.environ.get("SCHOLARTRACK_MIN_INTERVAL", "2.0"))

# Optional alert delivery; unset means "log only" (safe demo mode).
WEBHOOK_URL = os.environ.get("SCHOLARTRACK_WEBHOOK_URL", "")


def operator_token() -> str:
    """Token guarding operator/refresh endpoints. Env var wins; else a generated file in data/."""
    env = os.environ.get("SCHOLARTRACK_OPERATOR_TOKEN")
    if env:
        return env
    path = DB_PATH.parent / "operator_token.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(12), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()
