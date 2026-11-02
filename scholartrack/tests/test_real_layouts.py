"""Adapters for the real official-source layouts (NSP blocks, detail pages, paginated catalogue cards),
tested on small synthetic fixtures that copy each layout's structure. No network access."""
import sqlite3
from datetime import datetime, timezone

import pytest

from app import db
from app import normalize as nz
from app.adapters import BlockAdapter, CardAdapter, DetailPagesAdapter
from app.fetcher import FetchError, FetchResult
from app.pipeline import refresh_source
from conftest import add_source, fixture

TODAY = datetime(2026, 10, 21, tzinfo=timezone.utc)


class RoutedFetcher:
    """Serves fixture files by URL; a URL mapped to a FetchError raises it."""

    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def get(self, url):
        self.calls.append(url)
        target = self.routes.get(url)
        if isinstance(target, FetchError):
            raise target
        if target is None:
            raise FetchError("http_error", f"HTTP 404 for {url}", 404, 1)
        return FetchResult(url, 200, fixture(target), 1)


# ---------- normalisation additions

def test_dash_separated_day_first_dates():
    d = nz.parse_deadline("31-10-2026", "Asia/Kolkata")
    assert d.due_at == "2026-10-31T18:29:00Z" and d.tz_assumed and d.time_assumed


def test_year_inferred_only_from_a_matching_weekday():
    d = nz.parse_deadline("16:00 BST on Tuesday 20 October", today=TODAY)
    assert d.due_at == "2026-10-20T15:00:00Z" and d.year_inferred
    assert nz.parse_deadline("20 October", today=TODAY).due_at is None             # no weekday: never guess
    assert nz.parse_deadline("Sunday 20 October", today=TODAY).due_at is None      # no year in 2025-2027 fits


def test_levels_for_indian_and_research_terms():
    assert nz.normalize_level("Post Matric Scholarship") == "School, Undergraduate, Postgraduate"
    assert nz.normalize_level("Pre Matric Scholarship") == "School"
    assert nz.normalize_level("AICTE Pragati (Technical Diploma)") == "Diploma"
    assert nz.normalize_level("Fulbright-Nehru Postdoctoral Research Fellowships") == "Postdoctoral"
    assert nz.normalize_level("Fulbright-Nehru Doctoral Research Fellowships") == "Doctoral"
    assert nz.normalize_level("Top Class Education In Schools") == "School"


def test_subject_inference_and_categories():
    assert nz.infer_discipline("Joint Master in Sustainable Catalysis") == "Physics, chemistry & maths"
    assert nz.infer_discipline("Data Engineering and Artificial Intelligence") == "Computer science & AI"
    assert nz.infer_discipline("Sports Ethics and Integrity") == "All subjects / interdisciplinary"
    assert nz.normalize_category(None, "Example Scheme (Welfare Based Scheme)") == "Need-based"
    assert nz.normalize_category("Fully funded scholarship") == "Fully funded"


# ---------- blocks layout (National Scholarship Portal)

NSP_CONFIG = {"block": ".accordion-body > div.row", "title": "h6", "group_class": "accordion-item",
              "group_title": ".accordion-button", "link_text": "Specification",
              "badge_labels": {"student application open till": "due_text",
                               "student application open till (for renewal)": "due_text"}}


def test_block_adapter_reads_title_ministry_deadline_and_official_link():
    res = BlockAdapter().parse(fixture("blocks_nsp_like.html"), "https://portal.example/All", NSP_CONFIG)
    by_title = {n.title: n for n in res.notices}
    first = by_title["Example Post Matric Scholarship (Welfare Based Scheme)"]
    assert first.fields["provider"] == "Ministry of Example Affairs"
    assert first.fields["due_text"] == "31-10-2026"                      # not the institute-verification date
    assert first.url == "https://portal.example/guidelines/post-matric.pdf"
    renewal = by_title["Example Top Class Merit Scheme (Merit Based Scheme)"]
    assert renewal.fields["due_text"] == "30-11-2026"
    assert "renewal" in renewal.fields["eligibility_excerpt"].lower()
    assert renewal.url == "https://portal.example/All"                    # href="null" falls back to the listing
    assert "due_text" not in by_title["Example Research Fellowship (JRF)"].fields
    assert any("missing title" in w for w in res.warnings)


# ---------- detail pages layout (CSC UK, USIEF)

DETAIL_CONFIG = {"link_pattern": r"/awards/[a-z-]+/$", "exclude_pattern": "host-organisations",
                 "title_strip": r"\s*[–-]\s*information for candidates",
                 "deadline_rules": [{"type": "table_label", "label": "application deadline"},
                                    {"type": "sentence", "pattern": r"closing date for applications is (?P<when>[^.]+)"}]}
INDEX = "https://awards.example/awards/"


def detail_routes(broken=FetchError("timeout", "slow", None, 3)):
    return {INDEX: "detail_index.html",
            "https://awards.example/awards/masters-award/": "detail_masters.html",
            "https://awards.example/awards/phd-award/": "detail_phd.html",
            "https://awards.example/awards/broken-award/": broken}


def test_detail_adapter_follows_links_and_reads_deadlines_by_rule():
    out = DetailPagesAdapter().collect(RoutedFetcher(detail_routes()), INDEX, DETAIL_CONFIG)
    by_title = {n.title: n for n in out.notices}
    assert set(by_title) == {"Masters Award", "PhD Award"}                # host page excluded, broken one skipped
    assert by_title["Masters Award"].fields["due_text"] == "16:00 GMT on Tuesday 8 December 2026"
    assert by_title["PhD Award"].fields["due_text"] == "July 1, 2027"
    assert "eligibility_excerpt" not in by_title["Masters Award"].fields  # excerpts only when the source allows
    assert out.skipped_keys == {"https://awards.example/awards/broken-award/"}
    assert any("broken-award" in w for w in out.warnings)


def test_unreadable_detail_page_keeps_previous_data_instead_of_unlisting(conn):
    import json
    src_conf = json.dumps(DETAIL_CONFIG)
    conn.execute("INSERT INTO sources (source_id, name, listing_url, adapter, default_timezone, config_json) "
                 "VALUES ('d', 'Detail source', ?, 'detail_pages', 'Asia/Kolkata', ?)", (INDEX, src_conf))
    src = conn.execute("SELECT * FROM sources WHERE source_id = 'd'").fetchone()
    healthy = detail_routes(broken="detail_masters.html")
    refresh_source(conn, src, RoutedFetcher(healthy))
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE notice_status = 'active'").fetchone()[0] == 3
    out = refresh_source(conn, src, RoutedFetcher(detail_routes()))   # broken page times out this time
    assert out["status"] == "warning"
    statuses = {r[0] for r in conn.execute("SELECT notice_status FROM notices")}
    assert statuses == {"active"}                                       # nothing wrongly marked as removed


def test_detail_pages_end_to_end_values(conn):
    import json
    conn.execute("INSERT INTO sources (source_id, name, listing_url, adapter, default_timezone, config_json) "
                 "VALUES ('d', 'Detail source', ?, 'detail_pages', 'Asia/Kolkata', ?)",
                 (INDEX, json.dumps({**DETAIL_CONFIG, "defaults": {"funding_text": "Fully funded scholarship"}})))
    src = conn.execute("SELECT * FROM sources WHERE source_id = 'd'").fetchone()
    refresh_source(conn, src, RoutedFetcher(detail_routes()))
    m = conn.execute("SELECT * FROM notices WHERE title = 'Masters Award'").fetchone()
    assert m["due_at"] == "2026-12-08T16:00:00Z" and m["funding_category"] == "Fully funded"
    assert m["education_level"] == "Postgraduate"                      # from the page summary
    assert m["eligibility_excerpt"] is None                             # summary used, not stored
    p = conn.execute("SELECT * FROM notices WHERE title = 'PhD Award'").fetchone()
    assert p["due_at"] == "2027-07-01T18:29:00Z" and p["tz_assumed"] == 1


# ---------- paginated cards (Erasmus Mundus catalogue)

CARD_CONFIG = {"card": "article.ecl-card", "title": ".ecl-content-block__title", "next_selector": "li.next a",
               "max_pages": 5, "fallback_to_listing": True}
CAT = "https://catalogue.example/list"


def test_cards_follow_pagination_and_keep_unlinked_items_on_listing_url():
    f = RoutedFetcher({CAT: "cards_page1.html", CAT + "?page=1": "cards_page2.html"})
    out = CardAdapter().collect(f, CAT, CARD_CONFIG)
    assert out.complete and len(out.notices) == 4
    unlinked = [n for n in out.notices if n.title == "Programme Listed Without Link"][0]
    assert unlinked.url == CAT + "?page=1"


def test_failed_later_page_marks_run_incomplete():
    f = RoutedFetcher({CAT: "cards_page1.html", CAT + "?page=1": FetchError("http_error", "HTTP 503", 503, 3)})
    out = CardAdapter().collect(f, CAT, CARD_CONFIG)
    assert not out.complete and len(out.notices) == 2


def test_incomplete_run_does_not_unlist_notices(conn):
    import json
    conn.execute("INSERT INTO sources (source_id, name, listing_url, adapter, config_json) VALUES "
                 "('c', 'Catalogue', ?, 'cards', ?)", (CAT, json.dumps(CARD_CONFIG)))
    src = conn.execute("SELECT * FROM sources WHERE source_id = 'c'").fetchone()
    refresh_source(conn, src, RoutedFetcher({CAT: "cards_page1.html", CAT + "?page=1": "cards_page2.html"}))
    refresh_source(conn, src, RoutedFetcher({CAT: "cards_page1.html",
                                             CAT + "?page=1": FetchError("timeout", "slow", None, 3)}))
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE notice_status = 'unlisted'").fetchone()[0] == 0
    subjects = {r[0] for r in conn.execute("SELECT discipline FROM notices")}
    assert "Computer science & AI" in subjects


# ---------- migration 001 -> 002 keeps existing data

def test_migration_upgrades_existing_database(tmp_path):
    path = tmp_path / "old.db"
    old = db.connect(path)
    old.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    old.executescript("BEGIN;" + (db.MIGRATIONS_DIR / "001_init.sql").read_text(encoding="utf-8") + "COMMIT;")
    old.execute("INSERT INTO schema_migrations VALUES ('001_init', 'x')")
    add_source(old, "demo-portal-a")
    old.execute("INSERT INTO notices (notice_id, source_id, external_key, url, listing_url, title, captured_at, "
                "first_seen_at, verified_at, content_hash) VALUES ('n1', 'demo-portal-a', 'k', 'u', 'l', 't', "
                "'n', 'n', 'n', 'h')")
    assert db.migrate(old) == ["002_real_sources"]
    s = old.execute("SELECT * FROM sources").fetchone()
    assert s["is_demo"] == 1 and s["refresh_minutes"] == 60
    assert old.execute("SELECT year_inferred FROM notices").fetchone()[0] == 0
    old.execute("UPDATE sources SET adapter = 'detail_pages'")          # new adapter allowed
    with pytest.raises(sqlite3.IntegrityError):
        old.execute("UPDATE sources SET adapter = 'bogus'")
    with pytest.raises(sqlite3.IntegrityError):                         # foreign keys still enforced
        old.execute("INSERT INTO notices (notice_id, source_id, external_key, url, listing_url, title, captured_at, "
                    "first_seen_at, verified_at, content_hash) VALUES ('n2', 'nope', 'k', 'u', 'l', 't', 'n', 'n', 'n', 'h')")
    assert db.migrate(old) == []                                        # idempotent
