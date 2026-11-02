"""Idempotent persistence, version-aware change detection, withdrawal handling and freshness logic."""
import hashlib
import json
import logging
import sqlite3

from . import timeutil as T
from .sources.base import FetchError, ParseError

log = logging.getLogger("transitnotice.ingest")


# ---------------------------------------------------------------- status / freshness
def effective_status(row, now_iso):
    """Status as seen by a user *right now*, computed from the announced window (never from fetch success)."""
    st = row["status"]
    if st in ("withdrawn", "cancelled"):
        return st
    if row["valid_to"] and row["valid_to"] < now_iso:
        return "expired"
    if row["valid_from"] and row["valid_from"] > now_iso:
        return "upcoming"
    return "active"


def source_freshness(src, now=None):
    """('live'|'stale'|'error'|'never', human text). 'live' is only returned for a recent successful fetch."""
    now = now or T.utcnow()
    if not src["last_success_at"]:
        return "never", "Never successfully checked"
    age = (now - T.from_iso(src["last_success_at"])).total_seconds()
    limit = max(1800, src["refresh_seconds"] * 3)
    if src["consecutive_failures"] > 0:
        return "error", "Source unreachable - showing last verified data from %s" % T.human_age(src["last_success_at"], now)
    if age > limit:
        return "stale", "Stale - last verified %s" % T.human_age(src["last_success_at"], now)
    return "live", "Verified %s" % T.human_age(src["last_success_at"], now)


# ---------------------------------------------------------------- hashing / diff
def content_hash(n):
    canon = json.dumps([n.type, n.summary, n.valid_from, n.valid_to, sorted(n.route_ids), sorted(n.stations),
                        n.area, n.source_status], ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def describe_change(old, n, tz):
    """Human readable change_summary between stored row `old` and parsed notice `n`."""
    parts = []
    L = lambda iso: T.localize(iso, tz)
    if old["valid_to"] != n.valid_to:
        if old["valid_to"] and n.valid_to:
            word = "Extended" if n.valid_to > old["valid_to"] else "Shortened"
            parts.append("%s: end %s -> %s" % (word, L(old["valid_to"]), L(n.valid_to)))
        elif n.valid_to:
            parts.append("End date added: %s" % L(n.valid_to))
        else:
            parts.append("End date removed: now open-ended")
    if old["valid_from"] != n.valid_from:
        parts.append("Start changed: %s -> %s" % (L(old["valid_from"]), L(n.valid_from)))
    if old["type"] != n.type:
        parts.append("Type changed: %s -> %s" % (old["type"], n.type))
    if old["summary"] != n.summary:
        parts.append("Notice text updated")
    if old["route_id"] != ",".join(sorted(n.route_ids)):
        parts.append("Affected routes changed")
    if old["area"] != n.area:
        parts.append("Area changed")
    if n.source_status != "active" and old["status"] != n.source_status:
        parts.append("Marked %s by source" % n.source_status)
    return "; ".join(parts) or "Details updated"


# ---------------------------------------------------------------- persistence
def _write_children(conn, nid, routes, stations):
    conn.execute("DELETE FROM notice_routes WHERE notice_id=?", (nid,))
    conn.execute("DELETE FROM notice_stations WHERE notice_id=?", (nid,))
    conn.executemany("INSERT OR IGNORE INTO notice_routes VALUES (?,?)", [(nid, r) for r in routes])
    conn.executemany("INSERT OR IGNORE INTO notice_stations VALUES (?,?)", [(nid, s) for s in stations])


def _snapshot(conn, nid, now):
    conn.execute(
        """INSERT OR REPLACE INTO notice_revisions
           (notice_id, revision_id, content_hash, type, summary, valid_from, valid_to, status, route_id, area,
            observed_at, change_summary)
           SELECT notice_id, revision_id, content_hash, type, summary, valid_from, valid_to, status, route_id,
                  area, ?, change_summary FROM notices WHERE notice_id=?""", (now, nid))


def upsert_notice(conn, src, n, now):
    """Insert/refresh one notice. Returns ('new'|'revised'|'unchanged', notice_id, revision_id)."""
    nid = "%s:%s" % (src["source_key"], n.external_id)
    h = content_hash(n)
    routes = sorted(dict.fromkeys(n.route_ids))
    route_csv = ",".join(routes)
    old = conn.execute("SELECT * FROM notices WHERE notice_id=?", (nid,)).fetchone()
    if old is None:
        conn.execute(
            """INSERT INTO notices (notice_id, source_id, external_id, agency, route_id, area, source_url, type, summary,
               published_at, valid_from, valid_to, status, observed_at, last_confirmed_at, content_hash, revision_id,
               change_summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'First observed')""",
            (nid, src["source_id"], n.external_id, src["agency"], route_csv, n.area, n.source_url, n.type, n.summary,
             n.published_at, n.valid_from, n.valid_to, n.source_status, now, now, h))
        _write_children(conn, nid, routes, n.stations)
        _snapshot(conn, nid, now)
        return "new", nid, 1
    if old["content_hash"] == h and old["status"] != "withdrawn":
        conn.execute("UPDATE notices SET last_confirmed_at=?, source_url=? WHERE notice_id=?", (now, n.source_url, nid))
        return "unchanged", nid, old["revision_id"]
    summary = ("Reappeared in source listing" if old["content_hash"] == h
               else describe_change(old, n, src["timezone"]))
    rev = old["revision_id"] + 1
    conn.execute(
        """UPDATE notices SET route_id=?, area=?, source_url=?, type=?, summary=?, published_at=COALESCE(?,published_at),
           valid_from=?, valid_to=?, status=?, last_confirmed_at=?, content_hash=?, revision_id=?, change_summary=?
           WHERE notice_id=?""",
        (route_csv, n.area, n.source_url, n.type, n.summary, n.published_at, n.valid_from, n.valid_to,
         n.source_status, now, h, rev, summary, nid))
    _write_children(conn, nid, routes, n.stations)
    _snapshot(conn, nid, now)
    return "revised", nid, rev


def withdraw_missing(conn, src, seen_ids, now):
    """After a COMPLETE successful fetch, notices the source no longer lists are withdrawn (kept as history)."""
    rows = conn.execute("SELECT notice_id, revision_id FROM notices WHERE source_id=? AND status IN ('active','expired')",
                        (src["source_id"],)).fetchall()
    out = []
    for r in rows:
        if r["notice_id"] in seen_ids:
            continue
        rev = r["revision_id"] + 1
        conn.execute("UPDATE notices SET status='withdrawn', revision_id=?, change_summary=? WHERE notice_id=?",
                     (rev, "Removed from source listing (withdrawn)", r["notice_id"]))
        _snapshot(conn, r["notice_id"], now)
        out.append((r["notice_id"], rev))
    return out


def sweep_expired(conn, now):
    conn.execute("UPDATE notices SET status='expired' WHERE status='active' AND valid_to IS NOT NULL AND valid_to < ?", (now,))


def apply_result(conn, src, result, now):
    """Apply one successful fetch atomically. Returns (counts, changes[(notice_id, revision_id, kind)])."""
    counts = dict(new=0, revised=0, withdrawn=0)
    changes = []
    seen = {"%s:%s" % (src["source_key"], i) for i in result.unparsed_ids}
    with conn:
        for n in result.notices:
            kind, nid, rev = upsert_notice(conn, src, n, now)
            seen.add(nid)
            if kind != "unchanged":
                counts[kind] += 1
                changes.append((nid, rev, kind))
        for nid, rev in withdraw_missing(conn, src, seen, now):
            counts["withdrawn"] += 1
            changes.append((nid, rev, "withdrawn"))
        sweep_expired(conn, now)
    return counts, changes


# ---------------------------------------------------------------- one refresh of one source
def refresh_source(conn, cfg, adapter, fetcher, alerter=None, now=None):
    """Fetch -> parse -> persist. A failed refresh never touches notices or last_confirmed_at, so old data
    can never be mistaken for live data. Returns dict(outcome=..., ...)."""
    from .db import ensure_source
    now_dt = now or T.utcnow()
    now_iso = T.to_iso(now_dt)
    src = ensure_source(conn, adapter.source_key, adapter.agency, adapter.base_url, adapter.timezone,
                        adapter.refresh_seconds, getattr(adapter, "operator_type", "Government"))
    run_id = conn.execute("INSERT INTO fetch_runs (source_id, started_at) VALUES (?,?)",
                          (src["source_id"], now_iso)).lastrowid
    conn.execute("UPDATE sources SET last_attempt_at=? WHERE source_id=?", (now_iso, src["source_id"]))
    conn.commit()
    try:
        result = adapter.fetch(fetcher)
        if not result.notices and result.skipped:
            raise ParseError("all %d items failed to parse (%s)" % (result.skipped, "; ".join(result.diagnostics[:2])))
        counts, changes = apply_result(conn, src, result, now_iso)
    except (FetchError, ParseError, sqlite3.Error, Exception) as e:  # noqa: BLE001 - any failure must degrade gracefully
        fails = src["consecutive_failures"] + 1
        delay = min(300 * (2 ** min(fails, 10)), cfg.max_backoff_seconds)   # 10 min, 20 min, 40 min ... 6 h
        err = "%s: %s" % (type(e).__name__, e)
        log.error("refresh of %s failed (%d in a row): %s", adapter.source_key, fails, err)
        with conn:
            conn.execute("UPDATE fetch_runs SET finished_at=?, outcome='error', error=?, http_status=? WHERE run_id=?",
                         (T.now_iso(), err[:500], getattr(e, "http_status", None), run_id))
            conn.execute("UPDATE sources SET last_error=?, consecutive_failures=?, next_run_at=? WHERE source_id=?",
                         (err[:500], fails, T.to_iso(now_dt + T.timedelta(seconds=delay)), src["source_id"]))
        return dict(outcome="error", error=err, retry_in=delay)
    with conn:
        conn.execute("""UPDATE fetch_runs SET finished_at=?, outcome='ok', http_status=?, items_parsed=?, items_skipped=?,
                        items_new=?, items_revised=?, items_withdrawn=?, error=? WHERE run_id=?""",
                     (T.now_iso(), result.http_status, len(result.notices), result.skipped, counts["new"],
                      counts["revised"], counts["withdrawn"], "; ".join(result.diagnostics)[:500] or None, run_id))
        conn.execute("""UPDATE sources SET last_success_at=?, last_error=NULL, consecutive_failures=0, next_run_at=?
                        WHERE source_id=?""",
                     (now_iso, T.to_iso(now_dt + T.timedelta(seconds=adapter.refresh_seconds)), src["source_id"]))
    if alerter and changes:
        try:
            alerter.dispatch(conn, changes, now_iso)
        except Exception:  # alert problems must not fail ingestion
            log.exception("alert dispatch failed")
    return dict(outcome="ok", parsed=len(result.notices), skipped=result.skipped, diagnostics=result.diagnostics, **counts)
