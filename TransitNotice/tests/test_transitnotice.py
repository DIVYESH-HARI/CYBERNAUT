import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transitnotice import queries, timeutil as T                      # noqa: E402
from transitnotice.alerts import Alerter, matches                      # noqa: E402
from transitnotice.config import Config                                # noqa: E402
from transitnotice.db import open_db                                   # noqa: E402
from transitnotice.ingest import effective_status, refresh_source, source_freshness  # noqa: E402
from transitnotice.sources.base import FetchError, FetchResult, Fetcher, ParseError, ParsedNotice, RobotsDenied  # noqa: E402
from transitnotice.sources import build_adapters                       # noqa: E402
from transitnotice.sources.cmrl import CmrlAdapter, parse_detail       # noqa: E402
from transitnotice.sources.cmrl_press import classify, parse_press_page, title_dates  # noqa: E402
from transitnotice.sources.feed import FeedAdapter, find_window, parse_feed  # noqa: E402
from transitnotice.sources.mbta import parse_alerts                    # noqa: E402
from transitnotice.sources.sample_metro import parse_page              # noqa: E402

FIX = Path(__file__).parent / "fixtures"
NOW = T.from_iso("2026-11-02T12:00:00Z")


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


class StubAdapter:
    source_key, agency, base_url = "stub", "Stub Agency", "http://stub.test"
    timezone, refresh_seconds = "UTC", 600

    def __init__(self):
        self.notices, self.error = [], None

    def fetch(self, fetcher):
        if self.error:
            raise self.error
        return FetchResult(list(self.notices))


def notice(i="1", routes=("Red",), vf="2026-11-02T00:00:00Z", vt="2026-11-03T00:00:00Z", **kw):
    return ParsedNotice(external_id=i, summary=kw.pop("summary", "Notice %s" % i), type=kw.pop("type", "detour"),
                        route_ids=list(routes), area=kw.pop("area", "Area"), valid_from=vf, valid_to=vt,
                        source_url="http://stub.test/%s" % i, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(db_path=":memory:", log_dir=self.tmp.name, alert_mode="demo")
        self.conn = open_db(":memory:")
        self.ad = StubAdapter()

    def tearDown(self):
        self.tmp.cleanup()

    def refresh(self, now=NOW, alerter=None):
        return refresh_source(self.conn, self.cfg, self.ad, None, alerter, now=now)

    def notice_row(self, i="1"):
        return self.conn.execute("SELECT * FROM notices WHERE notice_id=?", ("stub:" + i,)).fetchone()


class AcceptanceTests(Base):
    def test_expired_advisory_not_marked_active(self):
        self.ad.notices = [notice("old", vf="2026-10-01T00:00:00Z", vt="2026-10-02T00:00:00Z"), notice("cur")]
        self.refresh()
        row = self.conn.execute("SELECT * FROM notices WHERE external_id='old'").fetchone()
        self.assertEqual(effective_status(row, T.to_iso(NOW)), "expired")
        current = {r["external_id"] for r in queries.search(self.conn, T.to_iso(NOW), status="current")}
        archive = {r["external_id"] for r in queries.search(self.conn, T.to_iso(NOW), status="archive")}
        self.assertEqual(current, {"cur"})
        self.assertEqual(archive, {"old"})

    def test_route_filter_with_multiple_affected_routes(self):
        self.ad.notices = [notice("a", routes=("Red", "Orange")), notice("b", routes=("Blue",))]
        self.refresh()
        for route, expected in (("Red", {"a"}), ("orange", {"a"}), ("Blue", {"b"}), ("Green", set())):
            got = {r["external_id"] for r in queries.search(self.conn, T.to_iso(NOW), route=route)}
            self.assertEqual(got, expected, route)

    def test_extended_advisory_updates_existing_history(self):
        self.ad.notices = [notice("x", vt="2026-11-03T00:00:00Z")]
        self.refresh()
        self.ad.notices = [notice("x", vt="2026-11-04T00:00:00Z")]
        res = self.refresh(now=NOW + timedelta(minutes=10))
        self.assertEqual(res["revised"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0], 1)   # same notice, not a new one
        row = self.notice_row("x")
        self.assertEqual(row["revision_id"], 2)
        self.assertIn("Extended", row["change_summary"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notice_revisions").fetchone()[0], 2)

    def test_timezone_handling(self):
        # naive source time is interpreted in the SOURCE zone, then stored as UTC
        self.assertEqual(T.parse_dt("2026-11-05 22:00", "Asia/Kolkata"), "2026-11-05T16:30:00Z")
        self.assertEqual(T.parse_dt("2026-11-05 22:00", "America/New_York"), "2026-11-06T03:00:00Z")   # EST (UTC-5)
        self.assertEqual(T.parse_dt("2026-06-05 22:00", "America/New_York"), "2026-06-06T02:00:00Z")   # EDT (UTC-4)
        self.assertEqual(T.parse_dt("2026-11-05T22:00:00-05:00", "Asia/Kolkata"), "2026-11-06T03:00:00Z")  # explicit offset wins
        self.assertEqual(T.parse_dt("2026-11-05", "Asia/Kolkata", end_of_day=True), "2026-11-05T18:29:59Z")
        self.assertIsNone(T.parse_dt("TBD", "UTC"))
        self.assertEqual(T.localize("2026-11-05T16:30:00Z", "Asia/Kolkata"), "05 Nov 2026 22:00 IST")

    def test_stale_notice_shows_latest_verification(self):
        self.ad.notices = [notice("s")]
        self.refresh(now=NOW)
        src = self.conn.execute("SELECT * FROM sources").fetchone()
        self.assertEqual(source_freshness(src, NOW + timedelta(minutes=5))[0], "live")
        state, text = source_freshness(src, NOW + timedelta(hours=5))
        self.assertEqual(state, "stale")
        self.assertIn("last verified", text)
        self.assertEqual(self.notice_row("s")["last_confirmed_at"], T.to_iso(NOW))

    def test_source_error_does_not_turn_old_data_into_live(self):
        self.ad.notices = [notice("e")]
        self.refresh(now=NOW)
        before = dict(self.notice_row("e"))
        self.ad.error = FetchError("HTTP 503", 503)
        res = self.refresh(now=NOW + timedelta(hours=1))
        self.assertEqual(res["outcome"], "error")
        after = self.notice_row("e")
        self.assertEqual(after["last_confirmed_at"], before["last_confirmed_at"])    # not bumped
        self.assertEqual(after["status"], "active")                                   # not withdrawn either
        src = self.conn.execute("SELECT * FROM sources").fetchone()
        self.assertEqual(src["consecutive_failures"], 1)
        self.assertEqual(source_freshness(src, NOW + timedelta(hours=1))[0], "error")
        self.assertEqual(self.conn.execute("SELECT outcome FROM fetch_runs ORDER BY run_id DESC").fetchone()[0], "error")


class PersistenceTests(Base):
    def test_refresh_is_idempotent(self):
        self.ad.notices = [notice("1"), notice("2", routes=("A", "B"))]
        self.refresh()
        snap = [tuple(r) for r in self.conn.execute("SELECT notice_id, revision_id, content_hash FROM notices ORDER BY 1")]
        res = self.refresh(now=NOW + timedelta(minutes=5))
        self.assertEqual((res["new"], res["revised"], res["withdrawn"]), (0, 0, 0))
        self.assertEqual(snap, [tuple(r) for r in self.conn.execute("SELECT notice_id, revision_id, content_hash FROM notices ORDER BY 1")])
        self.assertEqual(self.notice_row("1")["last_confirmed_at"], T.to_iso(NOW + timedelta(minutes=5)))

    def test_withdrawn_when_removed_and_reinstated_when_back(self):
        self.ad.notices = [notice("1"), notice("2")]
        self.refresh()
        self.ad.notices = [notice("1")]
        self.assertEqual(self.refresh(now=NOW + timedelta(minutes=5))["withdrawn"], 1)
        self.assertEqual(self.notice_row("2")["status"], "withdrawn")
        self.assertEqual(effective_status(self.notice_row("2"), T.to_iso(NOW)), "withdrawn")
        self.ad.notices = [notice("1"), notice("2")]
        self.refresh(now=NOW + timedelta(minutes=10))
        self.assertEqual(self.notice_row("2")["status"], "active")
        self.assertEqual(self.notice_row("2")["revision_id"], 3)

    def test_source_marked_cancelled(self):
        self.ad.notices = [notice("1")]
        self.refresh()
        self.ad.notices = [notice("1", source_status="cancelled")]
        self.refresh(now=NOW + timedelta(minutes=5))
        self.assertEqual(effective_status(self.notice_row("1"), T.to_iso(NOW)), "cancelled")
        self.assertEqual(len(queries.search(self.conn, T.to_iso(NOW), status="archive")), 1)

    def test_open_ended_advisory(self):
        self.ad.notices = [notice("o", vt=None)]
        self.refresh()
        self.assertEqual(effective_status(self.notice_row("o"), T.to_iso(NOW)), "active")
        self.ad.notices = [notice("o", vt="2026-11-04T00:00:00Z")]
        self.refresh(now=NOW + timedelta(minutes=5))
        self.assertIn("End date added", self.notice_row("o")["change_summary"])

    def test_unparseable_run_keeps_existing_data(self):
        self.ad.notices = [notice("1")]
        self.refresh()
        self.ad.error = ParseError("layout changed")
        self.assertEqual(self.refresh(now=NOW + timedelta(minutes=5))["outcome"], "error")
        self.assertEqual(self.notice_row("1")["status"], "active")

    def test_db_constraints(self):
        import sqlite3
        self.ad.notices = [notice("1")]
        self.refresh()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE notices SET type='bogus' WHERE notice_id='stub:1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE notices SET valid_to='2000-01-01T00:00:00Z' WHERE notice_id='stub:1'")

    def test_backoff_grows_with_consecutive_failures(self):
        self.ad.error = FetchError("down")
        a = self.refresh(now=NOW)["retry_in"]
        b = self.refresh(now=NOW)["retry_in"]
        self.assertGreater(b, a)


class AlertTests(Base):
    def setUp(self):
        super().setUp()
        self.alerter = Alerter(self.cfg)
        self.conn.execute("INSERT INTO subscribers (email, subscriber_filter, created_at) VALUES ('a@x.test', ?, 'x')",
                          (json.dumps({"routes": ["Red"]}),))
        self.conn.execute("INSERT INTO subscribers (email, subscriber_filter, created_at) VALUES ('b@x.test', '{}', 'x')")
        self.conn.commit()

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0]

    def test_single_alert_per_new_or_revised_advisory(self):
        self.ad.notices = [notice("1", routes=("Red",))]
        self.refresh(alerter=self.alerter)
        self.assertEqual(self.count(), 2)                       # both subscribers match once
        self.refresh(now=NOW + timedelta(minutes=5), alerter=self.alerter)
        self.refresh(now=NOW + timedelta(minutes=10), alerter=self.alerter)
        self.assertEqual(self.count(), 2)                       # repeated refreshes do not re-alert
        self.ad.notices = [notice("1", routes=("Red",), vt="2026-11-09T00:00:00Z")]
        self.refresh(now=NOW + timedelta(minutes=15), alerter=self.alerter)
        self.assertEqual(self.count(), 4)                       # one more per subscriber for the revision
        self.assertEqual(self.conn.execute("SELECT alert_status FROM notices").fetchone()[0], "logged")
        self.assertTrue((Path(self.tmp.name) / "alerts.log").exists())

    def test_subscriber_filter_matching(self):
        self.ad.notices = [notice("1", routes=("Blue",))]
        self.refresh(alerter=self.alerter)
        self.assertEqual(self.count(), 1)                       # only the unfiltered subscriber
        n = {"type": "detour", "area": "Greater Boston - Subway"}
        self.assertTrue(matches('{"areas":["boston"],"types":["Detour"]}', n, [], []))
        self.assertFalse(matches('{"stations":["Park Street"]}', n, [], ["Alewife"]))

    def test_new_but_already_expired_not_alerted(self):
        self.ad.notices = [notice("old", vf="2026-10-01T00:00:00Z", vt="2026-10-02T00:00:00Z")]
        self.refresh(alerter=self.alerter)
        self.assertEqual(self.count(), 0)


class ParserTests(unittest.TestCase):
    def parse(self, name):
        return parse_page(fixture(name), "http://x.test/demo-source/notices.html", "Asia/Kolkata")[:3]

    def test_ok_page(self):
        out, skipped, _ = self.parse("sample_ok.html")
        self.assertEqual((len(out), skipped), (2, 0))
        a = out[0]
        self.assertEqual(a.route_ids, ["Line 1", "Line 2"])
        self.assertEqual(a.stations, ["Central", "Riverside"])
        self.assertEqual(a.type, "detour")
        self.assertEqual(a.valid_to, "2026-11-05T16:30:00Z")
        self.assertTrue(a.source_url.endswith("notices.html#T-1"))
        self.assertIsNone(out[1].valid_to)                       # "TBD" -> open ended

    def test_malformed_html_does_not_crash(self):
        out, skipped, diag = self.parse("sample_malformed.html")
        self.assertEqual({n.external_id for n in out}, {"M-1", "M-2"})
        self.assertIn("Line 5", out[0].route_ids)

    def test_missing_fields_skipped_with_diagnostics(self):
        out, skipped, diag = self.parse("sample_missing_fields.html")
        self.assertEqual([n.external_id for n in out], ["F-1"])
        self.assertEqual(skipped, 4)
        self.assertTrue(any("valid_to before valid_from" in d for d in diag))

    def test_duplicates_collapsed(self):
        out, skipped, diag = self.parse("sample_duplicates.html")
        self.assertEqual((len(out), skipped), (1, 1))

    def test_layout_change_raises_but_declared_empty_is_fine(self):
        with self.assertRaises(ParseError):
            self.parse("sample_layout_changed.html")
        self.assertEqual(self.parse("sample_empty.html")[0], [])

    def test_mbta_fixture(self):
        doc = json.loads(fixture("mbta_alerts.json"))
        out, skipped, diag = parse_alerts(doc, {"place-alfcl": "Alewife"}, "America/New_York")
        self.assertEqual((len(out), skipped), (2, 2))
        a, b = out
        self.assertEqual(a.type, "suspension")
        self.assertEqual(sorted(a.route_ids), ["Mattapan", "Red"])
        self.assertIn("Alewife", a.stations)
        self.assertEqual(a.valid_from, "2026-11-02T11:00:00Z")
        self.assertEqual(a.valid_to, "2026-11-05T09:00:00Z")      # latest of several periods
        self.assertEqual(b.type, "accessibility")
        self.assertIsNone(b.valid_to)                              # period without end -> open ended


class StubFetcher:
    """Maps URL substrings to fixture files; records every URL requested."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for part, name in self.routes.items():
            if part in url:
                if name is None:
                    raise FetchError("simulated outage for %s" % url, 503)
                return 200, (FIX / name).read_bytes(), {"X-WP-TotalPages": "1"}
        raise FetchError("unexpected url %s" % url, 404)


CMRL_ROUTES = {
    "/wp-json/wp/v2/road-diversion": "cmrl_list.json",
    "/wp-json/wp/v2/rd-category": "cmrl_categories.json",
    "/road-diversion/sample-junction/": "cmrl_detail_list.html",
    "/road-diversion/test-salai/": "cmrl_detail_para.html",
    "/road-diversion/broken-page/": "cmrl_detail_broken.html",
}


class CmrlTests(Base):
    """Chennai Metro Rail (Tamil Nadu) adapter."""

    def setUp(self):
        super().setUp()
        self.cache = Path(self.tmp.name) / "cmrl_cache.json"

    def test_parse_detail_list_and_paragraph_layouts(self):
        d = parse_detail(fixture("cmrl_detail_list.html"))
        self.assertEqual(d["title"], "Sample Junction Traffic Diversion")
        self.assertEqual(d["start"], "March 30, 2025")
        self.assertEqual(d["package"], "C3 UG-02")
        self.assertEqual(d["updated"], "September 22, 2026")
        self.assertIn("diverted via Tank Street -> Main Road", d["description"])
        self.assertNotIn("Footer", d["description"])
        p = parse_detail(fixture("cmrl_detail_para.html"))
        self.assertIn("Kamaraj Street -> Nehru Street", p["description"])
        with self.assertRaises(ParseError):
            parse_detail(fixture("cmrl_detail_broken.html"))

    def test_fetch_builds_notices(self):
        res = CmrlAdapter(self.cache).fetch(StubFetcher(CMRL_ROUTES))
        self.assertEqual((len(res.notices), res.skipped, res.unparsed_ids), (2, 1, ["503"]))
        a = {n.external_id: n for n in res.notices}["501"]
        self.assertEqual(a.route_ids, ["Corridor 3"])
        self.assertEqual(a.stations, ["Sample Junction"])
        self.assertEqual(a.area, "Chennai - Corridor 3 (Madhavaram - SIPCOT)")
        self.assertEqual(a.type, "detour")
        self.assertEqual(a.valid_from, "2025-03-29T18:30:00Z")        # 30 Mar 2025 00:00 IST
        self.assertIsNone(a.valid_to)                                  # CMRL gives no end date
        b = {n.external_id: n for n in res.notices}["502"]
        self.assertEqual(b.stations, ["Test Salai & Main Road"])       # HTML entity decoded

    def test_detail_pages_cached_until_modified(self):
        CmrlAdapter(self.cache).fetch(StubFetcher(CMRL_ROUTES))
        f2 = StubFetcher(CMRL_ROUTES)
        CmrlAdapter(self.cache).fetch(f2)                              # fresh instance reads the cache file
        details = [u for u in f2.calls if "/road-diversion/" in u and "wp-json" not in u]
        self.assertEqual(details, ["https://chennaimetrorail.org/road-diversion/broken-page/"])  # only the uncached one

    def test_one_detail_page_down_keeps_that_notice_unverified(self):
        refresh_source(self.conn, self.cfg, CmrlAdapter(self.cache), StubFetcher(CMRL_ROUTES), now=NOW)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notices WHERE status='active'").fetchone()[0], 2)
        routes = dict(CMRL_ROUTES)
        routes["/road-diversion/test-salai/"] = None                   # one detail page times out
        self.cache.unlink()
        later = NOW + timedelta(hours=7)
        res = refresh_source(self.conn, self.cfg, CmrlAdapter(self.cache), StubFetcher(routes), now=later)
        self.assertEqual((res["outcome"], res["withdrawn"]), ("ok", 0))
        row = self.conn.execute("SELECT status, last_confirmed_at FROM notices WHERE notice_id='cmrl:502'").fetchone()
        self.assertEqual(tuple(row), ("active", T.to_iso(NOW)))          # kept, but NOT re-verified
        other = self.conn.execute("SELECT last_confirmed_at FROM notices WHERE notice_id='cmrl:501'").fetchone()[0]
        self.assertEqual(other, T.to_iso(later))

    def test_whole_site_down_is_an_error(self):
        refresh_source(self.conn, self.cfg, CmrlAdapter(self.cache), StubFetcher(CMRL_ROUTES), now=NOW)
        res = refresh_source(self.conn, self.cfg, CmrlAdapter(self.cache),
                             StubFetcher({"/wp-json/wp/v2/road-diversion": None}), now=NOW + timedelta(hours=7))
        self.assertEqual(res["outcome"], "error")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notices WHERE status='active'").fetchone()[0], 2)

    def test_progress_is_cached_even_when_a_page_fails(self):
        routes = dict(CMRL_ROUTES)
        routes["/road-diversion/test-salai/"] = None
        CmrlAdapter(self.cache).fetch(StubFetcher(routes))
        self.assertIn("501", json.loads(self.cache.read_text(encoding="utf-8")))

    def test_unparsable_item_is_not_withdrawn(self):
        ad = CmrlAdapter(self.cache)
        refresh_source(self.conn, self.cfg, ad, StubFetcher(CMRL_ROUTES), now=NOW)
        routes = dict(CMRL_ROUTES)
        routes["/road-diversion/test-salai/"] = "cmrl_detail_broken.html"   # layout of one page changes
        self.cache.unlink()
        res = refresh_source(self.conn, self.cfg, CmrlAdapter(self.cache), StubFetcher(routes),
                             now=NOW + timedelta(hours=7))
        self.assertEqual((res["outcome"], res["withdrawn"]), ("ok", 0))
        self.assertEqual(self.conn.execute("SELECT status FROM notices WHERE notice_id='cmrl:502'").fetchone()[0], "active")

    def test_empty_list_is_an_error_not_mass_withdrawal(self):
        f = StubFetcher({"/wp-json/wp/v2/road-diversion": "sample_empty_list.json"})
        (FIX / "sample_empty_list.json").write_text("[]", encoding="utf-8")
        try:
            with self.assertRaises(ParseError):
                CmrlAdapter(None).fetch(f)
        finally:
            (FIX / "sample_empty_list.json").unlink()

    def test_service_day_uses_india_time(self):
        # notice valid only 00:30-01:00 IST on 3 Nov == 19:00-19:30 UTC on 2 Nov
        self.ad.notices = [notice("ist", vf="2026-11-02T19:00:00Z", vt="2026-11-02T19:30:00Z")]
        self.refresh(now=NOW)
        on = lambda d: [r["external_id"] for r in queries.search(self.conn, T.to_iso(NOW), on_date=d, status="all")]
        self.assertEqual(on("2026-11-03"), ["ist"])
        self.assertEqual(on("2026-11-02"), [])


class CmrlPressTests(Base):
    """CMRL press releases -> passenger service notices."""
    STATIONS = {"Nandanam": "Blue Line", "Egmore": "Green Line", "Wimco Nagar Depot": "Blue Line", "Wimco Nagar": "Blue Line",
                "Puratchi Thalaivar Dr. M.G.Ramachandran Central": "Blue Line"}

    class ByTitle(dict):
        """Look a notice up by the beginning of its release title."""
        def __getitem__(self, prefix):
            return next(n for title, n in self.items() if title.startswith(prefix))

    def parse(self):
        out, skipped, diag = parse_press_page(fixture("cmrl_press.html"), "https://chennaimetrorail.org/press-release/",
                                              "Asia/Kolkata", self.STATIONS)
        return self.ByTitle((n.summary, n) for n in out), skipped, diag

    def test_only_passenger_notices_are_kept(self):
        by, skipped, diag = self.parse()
        self.assertEqual(len(by), 8)
        self.assertEqual(skipped, 1)                                 # the row dated just "2016"
        joined = " ".join(by)
        for corporate in ("Milestone", "crore passengers", "Contract"):
            self.assertNotIn(corporate, joined)

    def test_kinds_lines_and_stations(self):
        by, *_ = self.parse()
        nandanam = by["Permanent Closure of B4 Entry a"]
        self.assertEqual((nandanam.type, nandanam.stations, nandanam.route_ids), ("station_closure", ["Nandanam"], ["Blue Line"]))
        self.assertIsNone(nandanam.valid_to)                         # "Permanent" -> open ended
        self.assertEqual(by["Temporary Closure of Parking Fa"].route_ids, ["Green Line"])   # from Egmore station
        self.assertEqual(by["Phase 2 Corridor-4 Stage 1A & 1"].route_ids, ["Corridor 4"])
        self.assertEqual(by["CMRL Increases Train Frequency "].route_ids, ["Blue Line"])
        amen = by["Additional Passenger Amenities "]
        self.assertEqual(amen.type, "other")
        self.assertEqual((amen.route_ids, amen.area), (["Blue Line"], "Chennai - Blue Line"))   # 2 stations, 1 line
        self.assertEqual(by["Changes in Train Service Timin"].type, "construction")

    def test_windows_from_titles_and_default_period(self):
        by, *_ = self.parse()
        m = by["Changes in Train Service Timin"]
        self.assertEqual((m.valid_from, m.valid_to), ("2025-10-19T18:30:00Z", "2025-10-24T18:29:59Z"))
        h = by["Metro Trains will operate on S"]
        self.assertEqual((h.valid_from[:10], h.valid_to[:10]), ("2025-01-13", "2025-01-16"))   # Jan 14-16 IST
        early = by["Earlier Metro Train Service: S"]
        self.assertEqual(early.valid_from[:10], "2026-03-19")       # "March 20" after a Dec 2025 release -> 2026
        r = by["Phase 2 Corridor-4 Stage 1A & 1"]                    # no date in title: 14 days after release
        self.assertEqual((r.valid_from, r.valid_to), ("2026-10-20T18:30:00Z", "2026-11-04T18:29:59Z"))
        self.assertTrue(by["CMRL Increases Train Frequency "].source_url.endswith("/press-release/"))  # '#' link

    def test_title_dates_and_classify(self):
        from datetime import date
        self.assertEqual(title_dates("on 5th January 2025", date(2025, 1, 3)), [date(2025, 1, 5)])
        self.assertEqual(title_dates("between 09.09.2025 and 19.10.2025", date(2025, 9, 5)),
                         [date(2025, 9, 9), date(2025, 10, 19)])
        self.assertEqual(title_dates("no dates", date(2025, 1, 1)), [])
        self.assertIsNone(classify("TBM Breakthrough at Kilpauk"))
        self.assertEqual(classify("Extended Train Service for the cricket match"), "schedule_change")
        self.assertEqual(classify("Free shuttle services from the Metro Station to Cricket Stadium"), "schedule_change")
        self.assertEqual(classify("Closing of parking in Meenambakkam Metro Station"), "station_closure")

    def test_missing_table_is_a_parse_error(self):
        with self.assertRaises(ParseError):
            parse_press_page("<html><body>maintenance</body></html>", "https://x/", "Asia/Kolkata")


class FeedTests(Base):
    """Generic RSS/Atom adapter used for private operators."""

    def test_rss_feed(self):
        out, skipped, diag, unparsed = parse_feed((FIX / "feed_rss.xml").read_bytes(), "Asia/Kolkata",
                                                  area="TN intercity", base_url="https://bus.test/feed.xml")
        by = {n.external_id: n for n in out}
        self.assertEqual(sorted(by), ["R-1", "R-2", "R-3", "R-4"])
        self.assertEqual(skipped, 3)                                  # duplicate, empty, backwards window
        r1 = by["R-1"]
        self.assertEqual(r1.route_ids, ["Chennai - Madurai", "Chennai - Tirunelveli"])
        self.assertEqual(r1.valid_from, "2026-11-01T12:30:00Z")       # "from 01 Nov 2026 18:00" IST
        self.assertIsNone(r1.valid_to)                                 # "until further notice"
        self.assertEqual(r1.source_url, "https://bus.test/notices/r-1")
        self.assertNotIn("<p>", r1.summary)
        self.assertEqual((by["R-2"].valid_from, by["R-2"].valid_to), ("2026-11-04T18:30:00Z", "2026-11-05T18:29:59Z"))
        self.assertEqual(by["R-2"].type, "suspension")                 # "not running"
        self.assertEqual(by["R-3"].type, "delay")
        self.assertEqual(by["R-4"].source_status, "cancelled")
        self.assertEqual(r1.area, "TN intercity")

    def test_atom_feed(self):
        out, *_ = parse_feed((FIX / "feed_atom.xml").read_bytes(), "Asia/Kolkata")
        self.assertEqual(len(out), 1)
        a = out[0]
        self.assertEqual((a.external_id, a.route_ids, a.type), ("urn:a-1", ["Route 5"], "detour"))
        self.assertEqual(a.valid_to, "2026-11-04T18:29:59Z")

    def test_not_a_feed(self):
        with self.assertRaises(ParseError):
            parse_feed(b"<html><body>maintenance</body></html>", "UTC")
        with self.assertRaises(ParseError):
            parse_feed(b"not xml at all <", "UTC")

    def test_find_window_phrases(self):
        self.assertEqual(find_window("closed on 5 November 2026", "Asia/Kolkata"),
                         ("2026-11-04T18:30:00Z", "2026-11-05T18:29:59Z"))
        self.assertEqual(find_window("no dates here", "UTC"), (None, None))
        self.assertEqual(find_window("valid till 2026-11-10", "UTC"), (None, "2026-11-10T23:59:59Z"))

    def test_private_operator_from_registry(self):
        reg = [{"key": "pb", "adapter": "rss", "agency": "Test Private Bus", "operator_type": "Private",
                "url": "{demo_base_url}/x.xml", "enabled": True},
               {"key": "off", "adapter": "rss", "agency": "Off", "url": "http://x", "enabled": False}]
        cfg = Config(db_path=":memory:", sources=None, demo_base_url="http://127.0.0.1:9")
        ads = build_adapters(cfg, reg)
        self.assertEqual([a.source_key for a in ads], ["pb"])
        self.assertEqual((ads[0].operator_type, ads[0].base_url), ("Private", "http://127.0.0.1:9/x.xml"))
        f = StubFetcher({"/x.xml": "feed_rss.xml"})
        refresh_source(self.conn, self.cfg, ads[0], f, now=NOW)
        self.assertEqual(self.conn.execute("SELECT operator_type FROM sources WHERE source_key='pb'").fetchone()[0], "Private")
        got = queries.search(self.conn, T.to_iso(NOW), operator="Private", status="all")
        self.assertEqual(len(got), 4)
        self.assertEqual(queries.search(self.conn, T.to_iso(NOW), operator="Government", status="all"), [])

    def test_real_registry_file_is_valid(self):
        cfg = Config(db_path=":memory:", sources=None)
        keys = [a.source_key for a in build_adapters(cfg)]
        self.assertIn("cmrl", keys)
        self.assertIn("demo_private_bus", keys)
        self.assertNotIn("mbta", keys)


class FacetTests(Base):
    """Every option offered in a dropdown must return at least one notice (and exactly the count shown)."""

    def setUp(self):
        super().setUp()
        self.ad.notices = [
            notice("a", routes=("Red", "Orange"), area="North", type="detour", stations=["Central"]),
            notice("b", routes=("Blue",), area="South", type="delay", vf="2026-11-03T00:00:00Z",
                   vt="2026-11-04T00:00:00Z"),
            notice("c", routes=("Red",), area="South", type="suspension", vt=None),
            notice("old", routes=("Green",), area="West", type="detour", vf="2026-10-01T00:00:00Z",
                   vt="2026-10-02T00:00:00Z"),
        ]
        self.refresh()
        self.now = T.to_iso(NOW)

    def check_all_options(self, base):
        opts = queries.facet_options(self.conn, self.now, base, "UTC")
        for key, items in opts.items():
            for o in items:
                if o["value"] == base.get(key):
                    continue
                got = queries.search_f(self.conn, self.now, {**base, key: o["value"]}, "UTC", limit=None)
                self.assertGreater(o["count"], 0, (key, o))
                self.assertEqual(len(got), o["count"], (base, key, o))
        return opts

    def test_every_option_has_results_with_no_filters(self):
        opts = self.check_all_options({})
        self.assertEqual([o["value"] for o in opts["route"]], ["Blue", "Orange", "Red"])   # Green only in ended notice
        self.assertNotIn("West", [o["value"] for o in opts["area"]])

    def test_options_shrink_with_other_filters(self):
        opts = self.check_all_options({"route": "Red"})
        self.assertEqual({o["value"] for o in opts["area"]}, {"North", "South"})
        self.assertEqual({o["value"] for o in opts["type"]}, {"detour", "suspension"})
        opts = self.check_all_options({"route": "Red", "area": "South"})
        self.assertEqual([o["value"] for o in opts["type"]], ["suspension"])
        self.check_all_options({"status": "archive"})
        self.check_all_options({"date": "2026-11-03"})

    def test_year_end_date_filters_and_sort(self):
        self.check_all_options({"year": "2026"})
        self.check_all_options({"ends": "open"})
        opts = queries.facet_options(self.conn, self.now, {}, "UTC", default_status="all")
        self.assertEqual([o["value"] for o in opts["year"]], ["2026"])
        self.assertEqual({o["value"]: o["count"] for o in opts["ends"]}, {"fixed": 3, "open": 1})
        ids = lambda **kw: [r["external_id"] for r in queries.search(self.conn, self.now, status="all", **kw)]
        self.assertEqual(ids(ends="open"), ["c"])
        self.assertEqual(ids(year="2025"), [])
        self.assertEqual(ids(sort="relevant")[-1], "old")            # ended notices after current ones
        self.assertEqual(ids(sort="oldest")[0], "old")
        self.assertEqual(ids(sort="ending")[-1], "c")                # open ended last

    def test_status_counts(self):
        st = {o["value"]: o["count"] for o in queries.facet_options(self.conn, self.now, {}, "UTC")["status"]}
        self.assertEqual(st, {"current": 3, "active": 2, "upcoming": 1, "archive": 1, "all": 4})

    def test_selected_value_kept_even_if_no_longer_matching(self):
        opts = queries.facet_options(self.conn, self.now, {"route": "Purple"}, "UTC")
        purple = [o for o in opts["route"] if o["value"] == "Purple"]
        self.assertEqual([(o["count"], o["group"]) for o in purple], [(0, "Your choice")])


class FakeResp:
    def __init__(self, status, body):
        self.status, self._b, self.headers = status, body, {}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FetcherTests(unittest.TestCase):
    def make(self, handler):
        self.sleeps, self.calls = [], []

        def opener(req, timeout=None):
            self.calls.append(req.full_url)
            return handler(req.full_url)
        cfg = Config(min_request_interval=0, max_attempts=3, backoff_base=2.0)
        return Fetcher(cfg, sleep=self.sleeps.append, opener=opener)

    def test_retry_ceiling_and_exponential_backoff(self):
        def handler(url):
            if url.endswith("robots.txt"):
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)
            raise urllib.error.HTTPError(url, 503, "down", {}, None)
        f = self.make(handler)
        with self.assertRaises(FetchError):
            f.get("http://h.test/page")
        self.assertEqual(len([c for c in self.calls if c.endswith("/page")]), 3)       # ceiling honoured
        self.assertEqual(self.sleeps, [2.0, 4.0])

    def test_no_retry_on_404(self):
        def handler(url):
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        f = self.make(handler)
        with self.assertRaises(FetchError):
            f.get("http://h.test/page")
        self.assertEqual(len([c for c in self.calls if c.endswith("/page")]), 1)

    def test_robots_disallow_respected(self):
        def handler(url):
            if url.endswith("robots.txt"):
                return FakeResp(200, b"User-agent: *\nDisallow: /private\n")
            return FakeResp(200, b"ok")
        f = self.make(handler)
        self.assertEqual(f.get("http://h.test/public")[1], b"ok")
        with self.assertRaises(RobotsDenied):
            f.get("http://h.test/private/x")

    def test_unreadable_robots_is_conservative(self):
        def handler(url):
            raise urllib.error.HTTPError(url, 500, "x", {}, None)
        f = self.make(handler)
        with self.assertRaises(RobotsDenied):
            f.get("http://h.test/page")


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from transitnotice.app import create_app
        self.cfg = Config(db_path=str(Path(self.tmp.name) / "t.db"), scheduler=False, admin_token="tok",
                          log_dir=self.tmp.name, sources=[])
        self.app = create_app(self.cfg, start_scheduler=False)
        self.client = self.app.test_client()
        conn = open_db(self.cfg.db_path)
        ad = StubAdapter()
        ad.notices = [notice("1", routes=("Red", "Orange"), vt=T.to_iso(T.utcnow() + timedelta(days=1)),
                             vf=T.to_iso(T.utcnow() - timedelta(days=1)))]
        refresh_source(conn, self.cfg, ad, None)
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_dashboard_and_filters(self):
        r = self.client.get("/?route=Orange")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Notice 1", r.data)
        self.assertIn(b"checked", r.data)
        r = self.client.get("/?route=Blue")                  # value that matches nothing must not break the page
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"Notice 1", r.data)
        self.assertIn(b"No notices match", r.data)
        self.assertIn(b"Route: Blue", r.data)                 # shown as a removable chip

    def test_filters_are_dropdowns_with_known_values(self):
        html = self.client.get("/").data.decode()
        self.assertIn('<select name="route">', html)
        self.assertIn('<optgroup label="Stub Agency">', html)
        self.assertIn('<option value="Orange"', html)
        self.assertIn('<select name="date">', html)
        self.assertIn("Today (", html)
        self.assertNotIn('<input name="route"', html)

    def test_subscribe_with_checkboxes(self):
        self.assertIn(b'type="checkbox" name="routes" value="Red"', self.client.get("/subscribe").data)
        self.client.post("/subscribe", data={"email": "c@d.co", "routes": ["Red", "Orange"], "types": ["detour"]})
        conn = open_db(self.cfg.db_path)
        flt = json.loads(conn.execute("SELECT subscriber_filter FROM subscribers WHERE email='c@d.co'").fetchone()[0])
        conn.close()
        self.assertEqual((flt["routes"], flt["types"]), (["Red", "Orange"], ["detour"]))

    def test_api_and_detail(self):
        j = self.client.get("/api/notices").get_json()
        self.assertEqual(j["count"], 1)
        self.assertEqual(j["notices"][0]["status"], "active")
        self.assertEqual(j["notices"][0]["source_freshness"], "live")
        self.assertEqual(self.client.get("/notice/stub:1").status_code, 200)
        self.assertEqual(self.client.get("/notice/nope").status_code, 404)

    def test_admin_requires_token(self):
        self.assertEqual(self.client.post("/admin/refresh").status_code, 403)
        self.assertEqual(self.client.get("/admin/alerts").status_code, 403)
        self.assertEqual(self.client.get("/admin/alerts?token=tok").status_code, 200)

    def test_subscribe_validates_email(self):
        self.assertIn(b"valid email", self.client.post("/subscribe", data={"email": "nope"}).data)
        self.assertIn(b"Subscribed", self.client.post("/subscribe", data={"email": "a@b.co", "routes": "Red"}).data)

    def test_html_is_escaped(self):
        conn = open_db(self.cfg.db_path)
        ad = StubAdapter()
        ad.notices = [notice("xss", summary="<script>alert(1)</script>", vf=T.to_iso(T.utcnow() - timedelta(days=1)),
                             vt=T.to_iso(T.utcnow() + timedelta(days=1)))]
        refresh_source(conn, self.cfg, ad, None)
        conn.close()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"&lt;script&gt;", r.data)
        self.assertNotIn(b"<script>alert(1)", r.data)

    def test_every_page_and_every_dropdown_option_loads(self):
        import re as _re
        for path in ("/", "/?status=archive", "/?status=all", "/status", "/subscribe", "/api/notices"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        html = self.client.get("/").data.decode()
        # follow every option of every dropdown: each must load and show at least one notice
        for select_name, body in _re.findall(r'<select name="(\w+)">(.*?)</select>', html, _re.S):
            for value in _re.findall(r'<option value="([^"]+)"', body):
                page = self.client.get("/", query_string={select_name: value})
                self.assertEqual(page.status_code, 200, (select_name, value))
                self.assertNotIn(b"No notices match", page.data, (select_name, value))


if __name__ == "__main__":
    unittest.main()
