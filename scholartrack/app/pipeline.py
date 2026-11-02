"""Collect -> normalise -> idempotent upsert -> version-aware change detection -> alerts."""
import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from . import alerts as alert_module
from . import normalize as nz
from .adapters import ADAPTERS, CollectResult, RawNotice
from .db import bump_data_version, iso, utcnow
from .fetcher import FetchError, Fetcher

log = logging.getLogger("scholartrack.pipeline")
_source_locks: dict[str, threading.Lock] = {}


def notice_id_for(source_id: str, external_key: str) -> str:
    return hashlib.sha1(f"{source_id}|{external_key}".encode()).hexdigest()[:16]


def build_record(raw: RawNotice, source: sqlite3.Row, listing_url: str, now: str, config: dict | None = None) -> dict:
    config = config or {}
    # Source-level facts (e.g. "open to Indian citizens") fill fields the page itself does not state.
    f = {**(config.get("defaults") or {}), **{k: v for k, v in raw.fields.items() if v}}
    deadline = nz.parse_deadline(f.get("due_text"), source["default_timezone"])
    withdrawn = nz.is_withdrawn(f.get("status_text"), raw.extra_status, raw.title)
    benefit = nz.clean(f.get("benefit_text"), 500)
    eligibility = nz.clean(f.get("eligibility_excerpt"), int(config.get("excerpt_limit", 800)))
    summary = f.get("summary_text")        # page summary: used for classification only, never stored
    level = nz.normalize_level(f.get("education_text"), raw.title)
    if level == "Unspecified" and summary:
        level = nz.normalize_level(summary)
    category = nz.normalize_category(f.get("funding_text"), benefit, eligibility, raw.title)
    rec = {
        "notice_id": notice_id_for(source["source_id"], raw.external_key),
        "source_id": source["source_id"],
        "external_key": raw.external_key,
        "url": raw.url,
        "listing_url": listing_url,
        "notice_type": nz.notice_type_of(raw.title, f.get("funding_text")),
        "title": nz.clean(raw.title, 300),
        "provider": nz.clean(f.get("provider"), 200),
        "education_level": level,
        "discipline": nz.clean(f.get("discipline"), 200) or nz.infer_discipline(raw.title),
        "region": nz.clean(f.get("region"), 200),
        "residency_rule": nz.clean(f.get("residency_rule"), 300),
        "funding_category": category,
        "benefit_text": benefit,
        "eligibility_excerpt": eligibility,
        "eligibility_keywords": ", ".join(nz.eligibility_keywords(eligibility, f.get("residency_rule"), raw.title)),
        "due_at": deadline.due_at,
        "due_raw": deadline.raw or None,
        "timezone": deadline.timezone,
        "tz_assumed": int(deadline.tz_assumed),
        "time_assumed": int(deadline.time_assumed),
        "year_inferred": int(deadline.year_inferred),
        "notice_status": "withdrawn" if withdrawn else "active",
        "now": now,
    }
    rec["content_hash"] = nz.content_hash({k: rec[k] for k in (
        "title", "provider", "education_level", "discipline", "region", "residency_rule",
        "funding_category", "benefit_text", "eligibility_excerpt", "due_at", "notice_status")})
    return rec


def _classify(old: sqlite3.Row, new: dict) -> str:
    if old["notice_status"] != "withdrawn" and new["notice_status"] == "withdrawn":
        return "withdrawn"
    if old["notice_status"] == "withdrawn" and new["notice_status"] == "active":
        return "reinstated"
    if old["due_at"] != new["due_at"]:
        if old["due_at"] is None:
            return "deadline_set"
        return "deadline_removed" if new["due_at"] is None else "deadline_revised"
    return "details_updated"


ALERTING = {"deadline_revised", "deadline_set", "deadline_removed", "withdrawn", "reinstated"}


def _message(kind: str, rec: dict, old: sqlite3.Row, source_name: str) -> str:
    title = rec["title"]
    prev = nz.format_local(old["due_at"], old["timezone"]) or "no published deadline"
    new = nz.format_local(rec["due_at"], rec["timezone"]) or "no published deadline"
    text = {
        "deadline_revised": f"Deadline revised for '{title}': {prev} -> {new}",
        "deadline_set": f"Deadline published for '{title}': {new}",
        "deadline_removed": f"Deadline removed for '{title}' (was {prev}); treat it as unknown",
        "withdrawn": f"Notice withdrawn: '{title}'",
        "reinstated": f"Notice reinstated: '{title}' (deadline {new})",
    }[kind]
    return f"{text} [source: {source_name}]"


def upsert_notice(conn: sqlite3.Connection, rec: dict, source_name: str) -> str:
    """Returns 'new' | 'changed' | 'unchanged'. Safe to run repeatedly with the same input."""
    old = conn.execute("SELECT * FROM notices WHERE notice_id = ?", (rec["notice_id"],)).fetchone()
    now = rec["now"]
    cols = ("url", "listing_url", "notice_type", "title", "provider", "education_level", "discipline", "region",
            "residency_rule", "funding_category", "benefit_text", "eligibility_excerpt", "eligibility_keywords",
            "due_at", "due_raw", "timezone", "tz_assumed", "time_assumed", "year_inferred", "content_hash",
            "notice_status")
    if old is None:
        names = ["notice_id", "source_id", "external_key", *cols, "captured_at", "first_seen_at", "verified_at"]
        values = [rec[c] for c in names[:3 + len(cols)]] + [now, now, now]
        conn.execute(f"INSERT INTO notices ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", values)
        return "new"

    if old["content_hash"] == rec["content_hash"] and old["notice_status"] != "unlisted":
        # Same content: refresh the "last verified" stamp and the display-only fields (e.g. tz label).
        conn.execute("UPDATE notices SET verified_at = ?, url = ?, listing_url = ?, timezone = ?, due_raw = ? "
                     "WHERE notice_id = ?", (now, rec["url"], rec["listing_url"], rec["timezone"], rec["due_raw"],
                                             rec["notice_id"]))
        return "unchanged"

    if old["content_hash"] == rec["content_hash"]:   # reappeared after being unlisted, nothing else changed
        conn.execute("UPDATE notices SET notice_status = ?, verified_at = ? WHERE notice_id = ?",
                     (rec["notice_status"], now, rec["notice_id"]))
        return "unchanged"

    kind = _classify(old, rec)
    version = old["version"] + 1
    sets = ", ".join(f"{c} = ?" for c in cols)
    conn.execute(f"UPDATE notices SET {sets}, captured_at = ?, verified_at = ?, version = ? WHERE notice_id = ?",
                 [rec[c] for c in cols] + [now, now, version, rec["notice_id"]])
    cur = conn.execute(
        "INSERT INTO changes (notice_id, version, change_type, previous_deadline, updated_deadline, "
        "previous_local, updated_local, detected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (rec["notice_id"], version, kind, old["due_at"], rec["due_at"],
         nz.format_local(old["due_at"], old["timezone"]), nz.format_local(rec["due_at"], rec["timezone"]), now))
    if kind in ALERTING:
        # UNIQUE(change_id) on alerts + UNIQUE(notice_id, version) on changes => one alert per revision.
        conn.execute("INSERT INTO alerts (change_id, notice_id, message, created_at) VALUES (?, ?, ?, ?)",
                     (cur.lastrowid, rec["notice_id"], _message(kind, rec, old, source_name), now))
    return "changed"


def refresh_source(conn: sqlite3.Connection, source: sqlite3.Row, fetcher: Fetcher) -> dict:
    """One scheduled/manual refresh of one source. Never raises: failures are recorded and flagged."""
    lock = _source_locks.setdefault(source["source_id"], threading.Lock())
    if not lock.acquire(blocking=False):
        return {"source_id": source["source_id"], "status": "skipped", "error": "refresh already running"}
    started = iso(utcnow())
    run_id = conn.execute(
        "INSERT INTO scrape_runs (source_id, started_at, status) VALUES (?, ?, 'running')",
        (source["source_id"], started)).lastrowid
    summary = {"source_id": source["source_id"], "status": "ok", "new": 0, "changed": 0, "found": 0,
               "warnings": [], "error": None}
    http_status, attempts = None, 0
    try:
        config = json.loads(source["config_json"] or "{}")
        try:
            collected = ADAPTERS[source["adapter"]].collect(fetcher, source["listing_url"], config)
            http_status, attempts = collected.http_status, collected.attempts
            summary["warnings"] = collected.warnings
            if not collected.notices:
                raise FetchError("parse_empty", "page fetched but no notices could be parsed: "
                                 + "; ".join(collected.warnings or ["empty page"]), http_status, attempts)
        except FetchError as exc:
            summary.update(status="failed", error=f"{exc.kind}: {exc}")
            http_status, attempts = exc.status, exc.attempts
            collected = None
        if collected is not None:
            summary["found"] = len(collected.notices)
            _persist(conn, source, collected, config, summary)
    except Exception as exc:  # unexpected bug: record it, keep the scheduler alive
        log.exception("refresh crashed for %s", source["source_id"])
        summary.update(status="failed", error=f"internal: {exc}")
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    finally:
        _finish(conn, source, run_id, summary, http_status, attempts)
        lock.release()
    try:
        alert_module.deliver_pending(conn)
    except Exception:
        log.exception("alert delivery failed")
    return summary


def _persist(conn, source, collected: CollectResult, config: dict, summary) -> None:
    now = iso(utcnow())
    seen_ids = {notice_id_for(source["source_id"], k) for k in collected.skipped_keys}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for raw in collected.notices:
            rec = build_record(raw, source, collected.url, now, config)
            seen_ids.add(rec["notice_id"])
            outcome = upsert_notice(conn, rec, source["name"])
            if outcome == "new":
                summary["new"] += 1
            elif outcome == "changed":
                summary["changed"] += 1
        # Only after a COMPLETE successful read: anything we did not see is flagged (not deleted, not "withdrawn").
        rows = conn.execute("SELECT notice_id FROM notices WHERE source_id = ? AND notice_status = 'active'",
                            (source["source_id"],)).fetchall() if collected.complete else []
        for row in rows:
            if row["notice_id"] not in seen_ids:
                conn.execute("UPDATE notices SET notice_status = 'unlisted' WHERE notice_id = ?", (row["notice_id"],))
                summary["changed"] += 1
        if summary["new"] or summary["changed"]:
            bump_data_version(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if collected.warnings or not collected.complete:
        summary["status"] = "warning"


def _finish(conn, source, run_id, summary, http_status, attempts) -> None:
    now = iso(utcnow())
    failed = summary["status"] == "failed"
    conn.execute(
        "UPDATE scrape_runs SET finished_at = ?, status = ?, http_status = ?, attempts = ?, notices_found = ?, "
        "notices_new = ?, notices_changed = ?, warnings = ?, error = ? WHERE run_id = ?",
        (now, summary["status"] if summary["status"] != "skipped" else "failed", http_status, attempts,
         summary["found"] if "found" in summary else 0, summary.get("new", 0), summary.get("changed", 0),
         "\n".join(summary["warnings"]) or None, summary["error"], run_id))
    conn.execute(
        "UPDATE sources SET last_checked_at = ?, last_status = ?, last_error = ?, "
        "last_success_at = CASE WHEN ? THEN last_success_at ELSE ? END, "
        "consecutive_failures = CASE WHEN ? THEN consecutive_failures + 1 ELSE 0 END WHERE source_id = ?",
        (now, summary["status"] if summary["status"] != "skipped" else "failed", summary["error"],
         failed, now, failed, source["source_id"]))
    bump_data_version(conn)


def refresh_all(conn: sqlite3.Connection, fetcher: Fetcher, only: str | None = None,
                due_only: bool = False) -> list[dict]:
    """Refresh enabled sources. With due_only, only those whose own refresh interval has passed."""
    sql = "SELECT * FROM sources WHERE enabled = 1" + (" AND source_id = ?" if only else "")
    sources = conn.execute(sql, (only,) if only else ()).fetchall()
    if due_only:
        now = utcnow()
        sources = [s for s in sources if s["last_checked_at"] is None or
                   datetime.strptime(s["last_checked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                   + timedelta(minutes=next_check_minutes(s)) <= now]
    return [refresh_source(conn, s, fetcher) for s in sources]


def next_check_minutes(source) -> int:
    """A healthy source waits its normal interval. A failing one (e.g. no internet at start-up) is retried
    after 2, 4, 8, ... minutes, never more often and never later than its normal interval."""
    if source["last_status"] != "failed":
        return source["refresh_minutes"]
    return min(source["refresh_minutes"], 2 ** min(source["consecutive_failures"], 10))


def import_snapshot(conn: sqlite3.Connection, path) -> int:
    """Load bundled real notices for enabled sources that have no data yet (first start without internet).
    Rows keep their original capture/verification times, so the dashboard labels them by their true age.
    A later live refresh updates them like any other notice (same notice_id), raising alerts on changes."""
    try:
        snapshot = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return 0
    empty = {r["source_id"] for r in conn.execute(
        "SELECT source_id FROM sources s WHERE enabled = 1 AND is_demo = 0 AND NOT EXISTS "
        "(SELECT 1 FROM notices n WHERE n.source_id = s.source_id)")}
    rows = [r for r in snapshot.get("notices", []) if r.get("source_id") in empty]
    if not rows:
        return 0
    cols = [c[1] for c in conn.execute("PRAGMA table_info(notices)") if c[1] != "version"]
    conn.execute("BEGIN IMMEDIATE")
    try:
        for r in rows:
            present = [c for c in cols if c in r]
            conn.execute(f"INSERT OR IGNORE INTO notices ({', '.join(present)}) VALUES ({', '.join('?' * len(present))})",
                         [r[c] for c in present])
        bump_data_version(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log.info("loaded %d notices from the bundled snapshot for %s", len(rows), ", ".join(sorted(empty)))
    return len(rows)


def sync_sources(conn: sqlite3.Connection, definitions: list[dict], base_url: str, demo_mode: bool = False) -> None:
    """Load approved sources from sources.json. Upsert by id; never overwrites runtime status columns.
    Demo sources are only enabled in demo mode; sources removed from the file are disabled, not deleted."""
    listed = []
    for d in definitions:
        is_demo = bool(d.get("demo", False))
        enabled = bool(d.get("enabled", True)) and (demo_mode or not is_demo)
        listed.append(d["source_id"])
        conn.execute(
            "INSERT INTO sources (source_id, name, listing_url, adapter, default_timezone, enabled, is_demo, "
            "refresh_minutes, approved_by, approved_at, config_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET name = excluded.name, listing_url = excluded.listing_url, "
            "adapter = excluded.adapter, default_timezone = excluded.default_timezone, enabled = excluded.enabled, "
            "is_demo = excluded.is_demo, refresh_minutes = excluded.refresh_minutes, approved_by = excluded.approved_by, "
            "approved_at = excluded.approved_at, config_json = excluded.config_json",
            (d["source_id"], d["name"], d["listing_url"].replace("{BASE_URL}", base_url), d["adapter"],
             d.get("default_timezone", "UTC"), int(enabled), int(is_demo), int(d.get("refresh_minutes", 60)),
             d.get("approved_by"), d.get("approved_at"), json.dumps(d.get("config", {}))))
    if listed:
        conn.execute(f"UPDATE sources SET enabled = 0 WHERE source_id NOT IN ({', '.join('?' * len(listed))})", listed)
