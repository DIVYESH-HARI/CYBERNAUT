from app.fetcher import FetchError
from app.pipeline import refresh_source
from conftest import FakeFetcher, add_source, fixture


def run(conn, source, html=None, error=None):
    return refresh_source(conn, source, FakeFetcher(html or "", error))


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_first_run_stores_notices_with_source_and_capture_time(conn):
    src = add_source(conn)
    out = run(conn, src, fixture("table_ok.html"))
    assert out["new"] == 3 and out["status"] == "ok"
    row = conn.execute("SELECT * FROM notices WHERE title = 'Alpha Merit Scholarship'").fetchone()
    assert row["source_id"] == "s1" and row["captured_at"] and row["verified_at"]
    assert row["url"] == "http://example.test/n/t1"          # link to the original announcement
    assert row["due_at"] == "2027-03-31T18:29:00Z"


def test_unknown_deadline_stays_null(conn):
    run(conn, add_source(conn), fixture("table_ok.html"))
    row = conn.execute("SELECT due_at, timezone, due_raw FROM notices WHERE title = 'Beta Research Grant'").fetchone()
    assert row["due_at"] is None and row["timezone"] is None and row["due_raw"] == "To be announced"


def test_rerun_is_idempotent(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    out = run(conn, src, fixture("table_ok.html"))
    assert out["new"] == 0 and out["changed"] == 0
    assert count(conn, "notices") == 3 and count(conn, "changes") == 0 and count(conn, "alerts") == 0


def test_source_correction_is_an_update_not_a_new_notice(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    corrected = fixture("table_ok.html").replace("31 March 2027, 23:59 IST", "15 April 2027, 23:59 IST")
    out = run(conn, src, corrected)
    assert out["new"] == 0 and out["changed"] == 1 and count(conn, "notices") == 3
    change = conn.execute("SELECT * FROM changes").fetchone()
    assert change["change_type"] == "deadline_revised"
    assert change["previous_deadline"] == "2027-03-31T18:29:00Z" and change["updated_deadline"] == "2027-04-15T18:29:00Z"
    assert conn.execute("SELECT version FROM notices WHERE external_key = 't1'").fetchone()[0] == 2


def test_alert_fires_once_per_meaningful_revision(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    revised = fixture("table_ok.html").replace("31 March 2027", "15 April 2027")
    run(conn, src, revised)
    run(conn, src, revised)            # same revision seen again
    run(conn, src, revised)
    assert count(conn, "alerts") == 1
    assert conn.execute("SELECT delivery_status FROM alerts").fetchone()[0] == "delivered"
    run(conn, src, fixture("table_ok.html").replace("31 March 2027", "20 April 2027"))   # a new revision
    assert count(conn, "alerts") == 2


def test_reformatted_same_instant_is_not_a_revision(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    same_instant = fixture("table_ok.html").replace("31 March 2027, 23:59 IST", "2027-03-31T18:29:00Z")
    out = run(conn, src, same_instant)
    assert out["changed"] == 0 and count(conn, "alerts") == 0


def test_details_change_is_recorded_without_alert(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    run(conn, src, fixture("table_ok.html").replace("CGPA above 8", "CGPA above 9"))
    assert conn.execute("SELECT change_type FROM changes").fetchone()[0] == "details_updated"
    assert count(conn, "alerts") == 0


def test_deadline_set_and_withdrawal_alert(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    html = fixture("table_ok.html").replace("To be announced", "1 Jun 2027 10:00 UTC")
    html = html.replace("<td>Open</td><td>Alpha Trust", "<td>Withdrawn</td><td>Alpha Trust")
    run(conn, src, html)
    types = sorted(r[0] for r in conn.execute("SELECT change_type FROM changes"))
    assert types == ["deadline_set", "withdrawn"] and count(conn, "alerts") == 2
    assert conn.execute("SELECT notice_status FROM notices WHERE external_key = 't1'").fetchone()[0] == "withdrawn"


def test_withdrawn_notice_detected_on_first_sight(conn):
    run(conn, add_source(conn), fixture("table_ok.html"))
    assert conn.execute("SELECT notice_status FROM notices WHERE external_key = 't3'").fetchone()[0] == "withdrawn"


def test_unreachable_source_is_flagged_and_data_kept(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    out = run(conn, src, error=FetchError("http_error", "HTTP 503", 503, 3))
    assert out["status"] == "failed"
    s = conn.execute("SELECT * FROM sources").fetchone()
    assert s["last_status"] == "failed" and "503" in s["last_error"] and s["consecutive_failures"] == 1
    assert s["last_success_at"] is not None
    assert count(conn, "notices") == 3
    run_row = conn.execute("SELECT * FROM scrape_runs ORDER BY run_id DESC").fetchone()
    assert run_row["status"] == "failed" and run_row["http_status"] == 503 and run_row["attempts"] == 3
    run(conn, src, fixture("table_ok.html"))
    assert conn.execute("SELECT consecutive_failures, last_status FROM sources").fetchone()[:] == (0, "ok")


def test_layout_change_is_a_failure_not_silent_deletion(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    out = run(conn, src, fixture("layout_changed.html"))
    assert out["status"] == "failed" and "parse_empty" in out["error"]
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE notice_status = 'active'").fetchone()[0] == 2


def test_notice_missing_from_a_good_page_becomes_unlisted_then_returns(conn):
    src = add_source(conn)
    run(conn, src, fixture("table_ok.html"))
    without = fixture("table_ok.html").replace(
        '<tr data-id="t2"><td>Master\'s</td><td><a href="/n/t2">Beta Research Grant</a></td><td>To be announced</td>'
        "<td>Enrolled students</td><td>Open</td><td>Beta Inst</td></tr>", "")
    run(conn, src, without)
    assert conn.execute("SELECT notice_status FROM notices WHERE external_key = 't2'").fetchone()[0] == "unlisted"
    run(conn, src, fixture("table_ok.html"))
    assert conn.execute("SELECT notice_status FROM notices WHERE external_key = 't2'").fetchone()[0] == "active"
    assert count(conn, "changes") == 0


def test_malformed_page_keeps_good_rows_and_reports_warning(conn):
    out = run(conn, add_source(conn), fixture("table_malformed.html"))
    assert out["status"] == "warning" and out["new"] == 2
    bad = conn.execute("SELECT due_at FROM notices WHERE title = 'Bad Date Row'").fetchone()
    assert bad["due_at"] is None


def test_card_layout_end_to_end(conn):
    src = add_source(conn, "s2", "cards", "UTC")
    out = run(conn, src, fixture("cards_ok.html"))
    assert out["new"] == 2
    row = conn.execute("SELECT * FROM notices WHERE external_key = 'c1'").fetchone()
    assert row["due_at"] == "2027-04-15T17:00:00Z" and row["education_level"] == "Postgraduate"
    assert conn.execute("SELECT notice_status FROM notices WHERE external_key = 'c2'").fetchone()[0] == "withdrawn"


def test_database_constraints_reject_bad_values(conn):
    import sqlite3
    import pytest
    add_source(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO notices (notice_id, source_id, external_key, url, listing_url, title, captured_at, "
                     "first_seen_at, verified_at, content_hash, notice_status) VALUES "
                     "('x','s1','k','u','l','t','n','n','n','h','bogus')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO notices (notice_id, source_id, external_key, url, listing_url, title, captured_at, "
                     "first_seen_at, verified_at, content_hash) VALUES ('x','missing','k','u','l','t','n','n','n','h')")


def test_failed_sources_are_retried_quickly_with_backoff(conn):
    from app.pipeline import next_check_minutes
    add_source(conn)
    conn.execute("UPDATE sources SET refresh_minutes = 360")
    s = lambda: conn.execute("SELECT * FROM sources").fetchone()
    assert next_check_minutes(s()) == 360
    conn.execute("UPDATE sources SET last_status = 'failed', consecutive_failures = 1")
    assert next_check_minutes(s()) == 2
    conn.execute("UPDATE sources SET consecutive_failures = 4")
    assert next_check_minutes(s()) == 16
    conn.execute("UPDATE sources SET consecutive_failures = 50")
    assert next_check_minutes(s()) == 360          # never later than the normal interval


def test_snapshot_fills_only_empty_real_sources_and_live_data_updates_it(conn, tmp_path):
    import json
    from app.pipeline import import_snapshot
    src = add_source(conn)
    add_source(conn, "demo-x")
    conn.execute("UPDATE sources SET is_demo = 1 WHERE source_id = 'demo-x'")
    run(conn, src, fixture("table_ok.html"))
    snap_rows = [dict(r) for r in conn.execute("SELECT * FROM notices")]
    conn.execute("DELETE FROM notices")
    path = tmp_path / "snap.json"
    rows = snap_rows + [{**snap_rows[0], "notice_id": "zzz", "source_id": "demo-x", "external_key": "z"}]
    path.write_text(json.dumps({"notices": rows}), encoding="utf-8")
    assert import_snapshot(conn, path) == 3                       # demo source ignored
    assert import_snapshot(conn, path) == 0                       # already has data: nothing re-imported
    out = run(conn, src, fixture("table_ok.html").replace("31 March 2027", "15 April 2027"))
    assert out["new"] == 0 and out["changed"] == 1                # live data updates the snapshot rows
    assert import_snapshot(conn, tmp_path / "missing.json") == 0
