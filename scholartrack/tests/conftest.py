import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.fetcher import FetchError, FetchResult  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    return c


class FakeFetcher:
    """Returns canned HTML (or raises) instead of touching the network."""

    def __init__(self, html: str = "", error: FetchError | None = None):
        self.html, self.error = html, error

    def get(self, url: str) -> FetchResult:
        if self.error:
            raise self.error
        return FetchResult(url, 200, self.html, 1)


def add_source(conn, source_id="s1", adapter="table", tz="Asia/Kolkata", url="http://example.test/list"):
    conn.execute("INSERT INTO sources (source_id, name, listing_url, adapter, default_timezone) VALUES (?,?,?,?,?)",
                 (source_id, f"Source {source_id}", url, adapter, tz))
    return conn.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
