"""FastAPI app: public read API + token-protected operator API + static dashboard."""
import hmac
import json
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, demo_site
from . import normalize as nz
from .db import connect, iso, migrate, utcnow
from .fetcher import Fetcher
from .pipeline import import_snapshot, next_check_minutes, refresh_all, sync_sources

log = logging.getLogger("scholartrack")
STATIC = Path(__file__).resolve().parent.parent / "static"
SNAPSHOT = Path(__file__).resolve().parent.parent / "seed" / "snapshot.json"


def make_fetcher() -> Fetcher:
    # The fetcher waits FETCH_MIN_INTERVAL between requests to the same remote host (not to localhost).
    return Fetcher(config.USER_AGENT, timeout=config.FETCH_TIMEOUT, max_retries=config.FETCH_MAX_RETRIES,
                   backoff=config.FETCH_BACKOFF, min_interval=config.FETCH_MIN_INTERVAL)


class Scheduler(threading.Thread):
    """Every minute, refreshes the sources whose own interval (sources.json "refresh_minutes") has passed."""

    def __init__(self):
        super().__init__(daemon=True, name="scholartrack-scheduler")
        self.stop_event = threading.Event()

    def run(self):
        conn = connect(config.DB_PATH)
        fetcher = make_fetcher()
        wait = 3.0   # let the web server start listening before the first refresh
        while not self.stop_event.wait(wait):
            try:
                refresh_all(conn, fetcher, due_only=True)
            except Exception:
                log.exception("scheduled refresh failed")
            wait = 60.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = connect(config.DB_PATH)
    migrate(conn)
    definitions = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
    sync_sources(conn, definitions, config.BASE_URL, demo_mode=config.DEMO_MODE)
    if config.USE_SNAPSHOT:
        import_snapshot(conn, SNAPSHOT)
    conn.close()
    log.info("Operator token (also in %s): %s", config.DB_PATH.parent / "operator_token.txt", config.operator_token())
    scheduler = None
    if config.SCHEDULER_ENABLED:
        scheduler = Scheduler()
        scheduler.start()
    yield
    if scheduler:
        scheduler.stop_event.set()


app = FastAPI(title="ScholarTrack", lifespan=lifespan)


@app.middleware("http")
async def no_stale_pages(request, call_next):
    # The dashboard files change between versions; make browsers re-check them instead of using old copies.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
if config.DEMO_MODE:
    app.include_router(demo_site.router)

    @app.get("/robots.txt", include_in_schema=False)
    def root_robots():
        # Crawlers read robots.txt at the site root, so the demo site's rules live here.
        return PlainTextResponse("User-agent: *\nDisallow: /demo-source/private/\nDisallow: /api/operator/\n")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


def get_db():
    conn = connect(config.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def require_operator(x_operator_token: str | None = Header(default=None)):
    if not x_operator_token or not hmac.compare_digest(x_operator_token, config.operator_token()):
        raise HTTPException(401, "Operator token required (see data/operator_token.txt)")


# ---------- helpers

def _parse_iso(ts: str | None) -> datetime | None:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if ts else None


def _ago(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "never"
    secs = int((now - dt).total_seconds())
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def verification(row: sqlite3.Row, src_status: str, now: datetime, refresh_minutes: int = 60) -> dict:
    """The current verification state of a notice. A stale snapshot is never described as live."""
    verified = _parse_iso(row["verified_at"])
    age_min = (now - verified).total_seconds() / 60
    if row["notice_status"] == "withdrawn":
        code, label = "withdrawn", "Withdrawn by the source"
    elif row["notice_status"] == "unlisted":
        code, label = "unlisted", "No longer listed at the source - check the official page"
    elif src_status == "failed":
        code, label = "unreachable", f"Source unreachable at last check - last verified {_ago(verified, now)}"
    elif age_min > config.stale_after(refresh_minutes):
        code, label = "stale", f"Stale snapshot - last verified {_ago(verified, now)}"
    else:
        code, label = "verified", f"Verified against the source {_ago(verified, now)}"
    return {"code": code, "label": label}


def notice_dict(row: sqlite3.Row, now: datetime) -> dict:
    d = dict(row)
    due = _parse_iso(row["due_at"])
    d["deadline_state"] = "unknown" if due is None else ("closed" if due <= now else "open")
    d["due_local"] = nz.format_local(row["due_at"], row["timezone"])
    d["keywords"] = [k for k in (row["eligibility_keywords"] or "").split(", ") if k]
    d["verification"] = verification(row, row["src_status"], now, row["src_refresh"])
    d["source_name"] = row["src_name"]
    for k in ("src_status", "src_name", "src_refresh", "eligibility_keywords", "content_hash", "external_key"):
        d.pop(k, None)
    return d


NOTICE_SELECT = ("SELECT n.*, s.last_status AS src_status, s.name AS src_name, s.refresh_minutes AS src_refresh "
                 "FROM notices n JOIN sources s ON s.source_id = n.source_id WHERE s.enabled = 1")
LEVEL_ORDER = [name for name, _ in nz._LEVELS] + ["Unspecified"]


def _day_bounds(day: str, tz_name: str, end: bool) -> str:
    try:
        tz = ZoneInfo(tz_name)
        y, m, dd = (int(x) for x in day.split("-"))
        base = datetime(y, m, dd, tzinfo=tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Invalid date or timezone")
    if end:
        base = base + timedelta(days=1) - timedelta(seconds=1)
    return iso(base)


# ---------- public API

@app.get("/api/notices")
def list_notices(
    q: str = "", level: str = "", discipline: str = "", category: str = "", notice_type: str = "",
    due_from: str = "", due_to: str = "", tz: str = "UTC", within_days: int | None = Query(None, ge=0, le=3650),
    include_unknown: bool = True, include_withdrawn: bool = False, include_closed: bool = False,
    sort: str = "deadline", conn: sqlite3.Connection = Depends(get_db),
):
    """Exact matches for the filters. If there are none, the filters are relaxed one at a time (least
    important first) and the response says which were relaxed, so a student never hits a dead end."""
    now = utcnow()
    f = dict(q=q.strip(), level=level, discipline=discipline, category=category, notice_type=notice_type,
             due_from=due_from, due_to=due_to, tz=tz, within_days=within_days, include_unknown=include_unknown,
             include_withdrawn=include_withdrawn, include_closed=include_closed, sort=sort)
    rows = _query(conn, f, now)
    relaxed = []
    if not rows:
        rows, relaxed = _closest_matches(conn, f, now)
    return {"count": len(rows), "exact": not relaxed, "relaxed": relaxed,
            "notices": [notice_dict(r, now) for r in rows]}


def _relax_steps(f: dict) -> list[tuple[str, dict]]:
    has_dates = f["within_days"] is not None or f["due_from"] or f["due_to"]
    steps = []
    if not f["include_closed"]:
        steps.append(("also showing applications that have closed for this cycle", {"include_closed": True}))
    if has_dates:
        steps.append(("removed the deadline date filter", {"within_days": None, "due_from": "", "due_to": ""}))
    if not f["include_unknown"]:
        steps.append(("also showing notices without a published deadline", {"include_unknown": True}))
    for key, name in (("discipline", "programme / subject"), ("category", "funding type"),
                      ("notice_type", "notice type"), ("level", "study level")):
        if f[key]:
            steps.append((f'removed {name} "{f[key]}"', {key: ""}))
    if f["q"]:
        steps.append((f'removed the search words "{f["q"]}"', {"q": ""}))
    return steps


def _closest_matches(conn, f: dict, now) -> tuple[list, list[str]]:
    steps = _relax_steps(f)
    for label, change in steps:                       # first try relaxing a single filter
        rows = _query(conn, {**f, **change}, now)
        if rows:
            return rows, [label]
    current, applied = dict(f), []
    for label, change in steps:                       # then relax them cumulatively
        current.update(change)
        applied.append(label)
        rows = _query(conn, current, now)
        if rows:
            return rows, applied
    return [], applied


def _query(conn, f: dict, now) -> list:
    where, args = [], []
    if f["q"]:
        like = "%" + f["q"].replace("%", r"\%").replace("_", r"\_") + "%"
        where.append("(n.title LIKE ? ESCAPE '\\' OR n.provider LIKE ? ESCAPE '\\' OR n.discipline LIKE ? ESCAPE '\\' "
                     "OR n.eligibility_excerpt LIKE ? ESCAPE '\\')")
        args += [like] * 4
    if f["level"]:
        where.append("n.education_level LIKE ?")
        args.append(f"%{f['level']}%")
    for key, col in (("discipline", "discipline"), ("category", "funding_category"), ("notice_type", "notice_type")):
        if f[key]:
            where.append(f"n.{col} = ?")
            args.append(f[key])
    if not f["include_withdrawn"]:
        where.append("n.notice_status = 'active'")
    date_filter = []
    if f["within_days"] is not None:
        date_filter.append(("n.due_at > ?", iso(now)))
        date_filter.append(("n.due_at <= ?", iso(now + timedelta(days=f["within_days"]))))
    if f["due_from"]:
        date_filter.append(("n.due_at >= ?", _day_bounds(f["due_from"], f["tz"], end=False)))
    if f["due_to"]:
        date_filter.append(("n.due_at <= ?", _day_bounds(f["due_to"], f["tz"], end=True)))
    # A date filter never matches an unknown deadline; unknowns only appear when no date filter is set.
    if date_filter:
        where.append("(" + " AND ".join(c for c, _ in date_filter) + ")")
        args += [v for _, v in date_filter]
    else:
        if not f["include_closed"]:
            where.append("(n.due_at IS NULL OR n.due_at > ?)")
            args.append(iso(now))
        if not f["include_unknown"]:
            where.append("n.due_at IS NOT NULL")
    # Open deadlines first (soonest first), then unknown, then closed (most recently closed first).
    order = {"deadline": "CASE WHEN n.due_at IS NULL THEN 1 WHEN n.due_at <= ? THEN 2 ELSE 0 END, "
                         "CASE WHEN n.due_at > ? THEN n.due_at END ASC, n.due_at DESC, n.title",
             "updated": "n.captured_at DESC", "title": "n.title COLLATE NOCASE"}.get(f["sort"], "n.title")
    sql = NOTICE_SELECT + "".join(f" AND {w}" for w in where) + f" ORDER BY {order}"
    return conn.execute(sql, args + [iso(now)] * order.count("?")).fetchall()


@app.get("/api/notices/{notice_id}")
def get_notice(notice_id: str, conn: sqlite3.Connection = Depends(get_db)):
    now = utcnow()
    row = conn.execute(NOTICE_SELECT + " AND n.notice_id = ?", (notice_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Notice not found")
    history = conn.execute("SELECT change_type, previous_local, updated_local, previous_deadline, updated_deadline, "
                           "detected_at FROM changes WHERE notice_id = ? ORDER BY version DESC", (notice_id,)).fetchall()
    return {**notice_dict(row, now), "history": [dict(h) for h in history]}


@app.get("/api/filters")
def filters(conn: sqlite3.Connection = Depends(get_db)):
    """Only values that exist in the data, each with how many notices are open now (or have no published
    deadline) and how many exist in total - so the dropdowns never offer an option with nothing behind it."""
    now = iso(utcnow())
    rows = conn.execute("SELECT n.education_level, n.discipline, n.funding_category, "
                        "(n.due_at IS NULL OR n.due_at > ?) AS is_open FROM notices n JOIN sources s "
                        "ON s.source_id = n.source_id WHERE s.enabled = 1 AND n.notice_status = 'active'", (now,)).fetchall()
    counts = {"levels": {}, "disciplines": {}, "categories": {}}

    def add(bucket, value, is_open):
        if value:
            c = counts[bucket].setdefault(value, [0, 0])
            c[0] += is_open
            c[1] += 1

    for r in rows:
        for lv in (r["education_level"] or "").split(", "):
            add("levels", lv, r["is_open"])
        add("disciplines", r["discipline"], r["is_open"])
        add("categories", r["funding_category"], r["is_open"])

    def listing(bucket, order=None):
        keys = sorted(counts[bucket], key=lambda k: (order.index(k) if order and k in order else 99, k))
        return [{"value": k, "open": counts[bucket][k][0], "total": counts[bucket][k][1]} for k in keys]

    return {"levels": listing("levels", LEVEL_ORDER), "disciplines": listing("disciplines"),
            "categories": listing("categories")}


@app.get("/api/sources")
def public_sources(conn: sqlite3.Connection = Depends(get_db)):
    """Where the data comes from (for students), with freshness - no internal error details."""
    now = utcnow()
    out = []
    for s in conn.execute("SELECT s.*, (SELECT COUNT(*) FROM notices n WHERE n.source_id = s.source_id "
                          "AND n.notice_status = 'active') AS notice_count FROM sources s WHERE s.enabled = 1 "
                          "ORDER BY s.is_demo, s.name"):
        out.append({"name": s["name"], "url": s["listing_url"], "notice_count": s["notice_count"],
                    "is_demo": bool(s["is_demo"]), "checked": _ago(_parse_iso(s["last_success_at"]), now),
                    "refresh_every_min": s["refresh_minutes"], "reachable": s["last_status"] != "failed"})
    return {"sources": out}


@app.get("/api/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=200), conn: sqlite3.Connection = Depends(get_db)):
    now = utcnow()
    rows = conn.execute(
        "SELECT a.alert_id, a.message, a.created_at, c.change_type, c.previous_local, c.updated_local, "
        "n.notice_id, n.title, n.url, n.notice_status, n.verified_at, s.name AS src_name, s.last_status AS src_status, "
        "s.refresh_minutes AS src_refresh FROM alerts a JOIN changes c ON c.change_id = a.change_id "
        "JOIN notices n ON n.notice_id = a.notice_id JOIN sources s ON s.source_id = n.source_id "
        "WHERE s.enabled = 1 ORDER BY a.created_at DESC, a.alert_id DESC LIMIT ?",
        (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["verification"] = verification(r, r["src_status"], now, r["src_refresh"])
        for k in ("src_status", "notice_status", "src_refresh"):
            d.pop(k)
        out.append(d)
    return {"alerts": out}


@app.get("/api/status")
def status(conn: sqlite3.Connection = Depends(get_db)):
    """Freshness summary for the page header, plus data_version so open browsers know when to reload."""
    now = utcnow()
    sources = conn.execute("SELECT * FROM sources WHERE enabled = 1").fetchall()
    never = [s for s in sources if not s["last_success_at"]]
    failing = [s["name"] for s in sources if s["last_status"] == "failed"]
    stale = [s["name"] for s in sources if s["last_success_at"] and
             (now - _parse_iso(s["last_success_at"])).total_seconds() / 60 > config.stale_after(s["refresh_minutes"])]
    last_ok = [_parse_iso(s["last_success_at"]) for s in sources if s["last_success_at"]]
    oldest_ok = min(last_ok) if last_ok else None
    counts = conn.execute("SELECT COUNT(*), MAX(n.verified_at) FROM notices n JOIN sources s ON s.source_id = n.source_id "
                          "WHERE s.enabled = 1 AND n.notice_status = 'active'").fetchone()
    newest_verified = _parse_iso(counts[1])
    retry = min((next_check_minutes(s) for s in sources if s["last_status"] == "failed"), default=None)
    if not sources or len(never) == len(sources) and not failing:
        state, label = "never", "Collecting scholarships from official sources..."
    elif failing:
        shown = f"showing data verified {_ago(newest_verified, now)}" if counts[0] else "no data yet"
        state, label = "degraded", f"Can't reach {len(failing)} of {len(sources)} official sites - {shown}"
    elif stale:
        state, label = "stale", f"Stale data from {len(stale)} source(s) - oldest check {_ago(oldest_ok, now)}"
    elif never:
        state, label = "never", f"{len(sources) - len(never)} of {len(sources)} sources checked - collecting the rest..."
    else:
        state, label = "fresh", f"{len(sources)} official sources checked - oldest {_ago(oldest_ok, now)}"
    version = conn.execute("SELECT value FROM app_meta WHERE key = 'data_version'").fetchone()[0]
    return {"state": state, "label": label, "data_version": int(version), "now": iso(now),
            "oldest_success": iso(oldest_ok) if oldest_ok else None,
            "refresh_every_min": min((s["refresh_minutes"] for s in sources), default=60),
            "demo_mode": config.DEMO_MODE, "notice_count": counts[0], "failing": failing,
            "all_failing": bool(sources) and len(failing) == len(sources),
            "newest_verified": counts[1], "retry_in_min": retry}


_retry_lock = threading.Lock()
_last_retry = {"at": None}


@app.post("/api/retry")
def retry_unreachable():
    """Public 'Try again now': re-checks only sources that failed or were never checked, at most once a minute,
    in the background (the page polls /api/status and reloads when data arrives)."""
    now = utcnow()
    with _retry_lock:
        if _last_retry["at"] and (now - _last_retry["at"]).total_seconds() < 60:
            return {"started": False, "message": "A retry ran less than a minute ago - please wait a moment."}
        _last_retry["at"] = now

    def work():
        conn = connect(config.DB_PATH)
        try:
            for s in conn.execute("SELECT * FROM sources WHERE enabled = 1 AND last_status IN ('failed', 'never')").fetchall():
                refresh_all(conn, make_fetcher(), only=s["source_id"])
        finally:
            conn.close()

    threading.Thread(target=work, daemon=True, name="scholartrack-retry").start()
    return {"started": True, "message": "Checking the official sites again - this takes up to a minute."}


# Ready-made searches offered under the search box. Only those that currently match something are shown.
SUGGESTED_SEARCHES = [
    ("Engineering & technical", "engineer"), ("AICTE schemes", "AICTE"), ("For girls & women", "girl"),
    ("Students with disabilities", "disab"), ("Research fellowships", "research"), ("Agriculture (ICAR)", "ICAR"),
    ("Post-matric (class 11 to PG)", "post matric"), ("OBC students", "OBC"),
    ("Merit-based schemes", "merit based"), ("Welfare / need-based", "welfare"), ("Renewable energy", "energy"),
    ("Statistics", "statist"), ("Study in the USA (Fulbright)", "Fulbright"),
    ("Study in the UK (Commonwealth)", "Commonwealth"), ("Study in Europe (Erasmus Mundus)", "Erasmus"),
    ("Computer science & AI", "comput"), ("Health & medicine", "health"), ("Climate & environment", "climat"),
]


@app.get("/api/suggestions")
def suggestions(conn: sqlite3.Connection = Depends(get_db)):
    """Clickable choices for the search box, each with how many notices it finds (zero-result choices are left out)."""
    now = utcnow()
    base = {"q": "", "level": "", "discipline": "", "category": "", "notice_type": "", "due_from": "", "due_to": "",
            "tz": "UTC", "within_days": None, "include_unknown": True, "include_withdrawn": False,
            "include_closed": False, "sort": "title"}
    searches = []
    for label, term in SUGGESTED_SEARCHES:
        n = len(_query(conn, {**base, "q": term}, now))
        if n:
            searches.append({"label": label, "q": term, "count": n})
    providers = conn.execute(
        "SELECT n.provider, COUNT(*) AS c FROM notices n JOIN sources s ON s.source_id = n.source_id "
        "WHERE s.enabled = 1 AND n.notice_status = 'active' AND n.provider IS NOT NULL AND (n.due_at IS NULL OR n.due_at > ?) "
        "GROUP BY n.provider ORDER BY c DESC, n.provider LIMIT 10", (iso(now),)).fetchall()
    return {"searches": searches, "providers": [{"label": p["provider"], "q": p["provider"], "count": p["c"]}
                                                for p in providers]}


# ---------- operator API (token required)

@app.get("/api/operator/overview", dependencies=[Depends(require_operator)])
def operator_overview(conn: sqlite3.Connection = Depends(get_db)):
    now = utcnow()
    sources = []
    # Only active sources: switched-off ones (e.g. demo sources outside demo mode) would only confuse the operator.
    for s in conn.execute("SELECT * FROM sources WHERE enabled = 1 ORDER BY name").fetchall():
        d = {k: s[k] for k in s.keys() if k != "config_json"}
        d["checked_ago"] = _ago(_parse_iso(s["last_checked_at"]), now)
        d["needs_review"] = s["last_status"] in ("failed", "warning")
        d["notice_count"] = conn.execute("SELECT COUNT(*) FROM notices WHERE source_id = ?", (s["source_id"],)).fetchone()[0]
        sources.append(d)
    runs = [dict(r) for r in conn.execute(
        "SELECT r.*, s.name AS source_name FROM scrape_runs r JOIN sources s USING (source_id) "
        "WHERE s.enabled = 1 ORDER BY r.run_id DESC LIMIT 20")]
    deliveries = [dict(r) for r in conn.execute(
        "SELECT d.*, a.message FROM delivery_log d JOIN alerts a USING (alert_id) ORDER BY d.id DESC LIMIT 20")]
    return {"sources": sources, "runs": runs, "deliveries": deliveries}


@app.post("/api/operator/refresh", dependencies=[Depends(require_operator)])
def operator_refresh(source_id: str | None = None, conn: sqlite3.Connection = Depends(get_db)):
    results = refresh_all(conn, make_fetcher(), only=source_id)
    if source_id and not results:
        raise HTTPException(404, "Unknown or disabled source")
    return {"results": results}


@app.post("/api/operator/demo", dependencies=[Depends(require_operator)])
def operator_demo(payload: dict):
    if not config.DEMO_MODE:
        raise HTTPException(404, "Demo mode is off")
    try:
        demo_site.apply_action(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True}


@app.get("/api/operator/demo-items", dependencies=[Depends(require_operator)])
def demo_items():
    if not config.DEMO_MODE:
        raise HTTPException(404, "Demo mode is off")
    state = demo_site._load()
    return {"mode": state["mode"], "items": [{"key": i["key"], "title": i["title"], "portal": i["portal"],
                                              "deadline": i["deadline"], "status": i["status"]} for i in state["items"]]}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
