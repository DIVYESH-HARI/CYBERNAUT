"""Read side: filtered notice search and dropdown options. Window/status logic is done in SQL on UTC ISO strings.

Dropdown options are *faceted*: the options of each dropdown are computed from the notices that match all
the OTHER chosen filters, with a count, and options with zero matches are left out. So every option a user
can pick returns at least one notice.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import timeutil as T

# 'upcoming' / 'active' / 'expired' derived from the announced window at query time
STATUS_SQL = """CASE WHEN n.status IN ('withdrawn','cancelled') THEN n.status
                      WHEN n.valid_to IS NOT NULL AND n.valid_to < :now THEN 'expired'
                      WHEN n.valid_from IS NOT NULL AND n.valid_from > :now THEN 'upcoming'
                      ELSE 'active' END"""

STATUS_GROUPS = {"current": ("active", "upcoming"), "archive": ("expired", "withdrawn", "cancelled")}
SHOW_LABELS = [("all", "Everything"), ("current", "Now & upcoming"), ("active", "In effect now"),
               ("upcoming", "Starting soon"), ("archive", "Ended / removed")]
STATUS_LABELS = {"active": "In effect now", "upcoming": "Starts soon", "expired": "Ended",
                 "withdrawn": "Removed by agency", "cancelled": "Cancelled"}
STATUS_RANK = {"active": 0, "upcoming": 1, "expired": 2, "withdrawn": 3, "cancelled": 3}
TYPE_LABELS = {"detour": "Diversion / detour", "suspension": "Service suspended", "delay": "Delays",
               "station_closure": "Station / entry / parking closed", "schedule_change": "Service change / special trains",
               "construction": "Maintenance / engineering works", "accessibility": "Lift / accessibility",
               "other": "Facilities, tickets & other"}
ENDS_LABELS = {"fixed": "End date announced", "open": "No end date announced"}
SORT_LABELS = [("relevant", "Current first, then newest"), ("newest", "Newest first"), ("oldest", "Oldest first"),
               ("ending", "Ending soonest")]
FILTER_KEYS = ("agency", "operator", "route", "station", "area", "type", "date", "year", "ends", "status", "q")


def day_bounds(on_date, tz):
    """Local calendar day -> (start, end) as UTC ISO, so 'today' means today in the viewer's zone."""
    d = datetime.strptime(on_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tz))
    return T.to_iso(d), T.to_iso(d + timedelta(days=1) - timedelta(seconds=1))


def search(conn, now_iso, q="", route="", station="", area="", type_="", status="current", on_date="",
           limit=300, tz="Asia/Kolkata", agency="", operator="", year="", ends="", sort="relevant"):
    """status: current (active+upcoming) | active | upcoming | archive (expired/withdrawn/cancelled) | all | <exact>.
    on_date: YYYY-MM-DD - advisories whose window overlaps that service day in zone `tz`.
    agency: agency name. year: YYYY of the start (or publish) date. ends: fixed | open."""
    p = {"now": now_iso}
    where = ["s.enabled = 1"]
    if q:
        where.append("(n.summary LIKE :q OR n.route_id LIKE :q OR n.area LIKE :q OR n.agency LIKE :q)")
        p["q"] = "%" + q + "%"
    if agency:
        where.append("n.agency = :agency")
        p["agency"] = agency
    if year:
        where.append("substr(COALESCE(n.valid_from, n.published_at, n.observed_at), 1, 4) = :year")
        p["year"] = year
    if ends == "fixed":
        where.append("n.valid_to IS NOT NULL")
    elif ends == "open":
        where.append("n.valid_to IS NULL")
    if operator:
        where.append("s.operator_type = :operator")
        p["operator"] = operator
    if route:
        where.append("EXISTS (SELECT 1 FROM notice_routes r WHERE r.notice_id=n.notice_id AND r.route_id=:route COLLATE NOCASE)")
        p["route"] = route
    if station:
        where.append("EXISTS (SELECT 1 FROM notice_stations x WHERE x.notice_id=n.notice_id AND x.station=:st COLLATE NOCASE)")
        p["st"] = station
    if area:
        where.append("n.area = :area")
        p["area"] = area
    if type_:
        where.append("n.type=:type")
        p["type"] = type_
    if on_date:
        try:
            p["d0"], p["d1"] = day_bounds(on_date, tz)
            where.append("(n.valid_to IS NULL OR n.valid_to >= :d0) AND (n.valid_from IS NULL OR n.valid_from <= :d1)")
        except ValueError:
            pass                                             # ignore a malformed date instead of failing the page
    sql = ("SELECT n.*, s.timezone AS tz, s.source_key, s.operator_type, (%s) AS eff_status"
           " FROM notices n JOIN sources s USING(source_id)"
           " WHERE %s ORDER BY COALESCE(n.valid_from, n.published_at) DESC") % (STATUS_SQL, " AND ".join(where))
    rows = conn.execute(sql, p).fetchall()
    keep = None if status == "all" else STATUS_GROUPS.get(status, (status,))
    rows = [r for r in rows if keep is None or r["eff_status"] in keep]
    start = lambda r: r["valid_from"] or r["published_at"] or r["observed_at"]
    if sort == "oldest":
        rows.sort(key=start)
    elif sort == "ending":           # soonest end first; open-ended last
        rows.sort(key=lambda r: (r["valid_to"] is None, r["valid_to"] or ""))
    elif sort == "relevant":         # in effect now, then upcoming, then the rest; newest first inside each
        rows.sort(key=start, reverse=True)
        rows.sort(key=lambda r: STATUS_RANK.get(r["eff_status"], 9))
    # "newest" is the SQL order already
    return rows if limit is None else rows[:limit]


def search_f(conn, now_iso, f, tz, limit=300, default_status="current"):
    """search() driven by a dict of request filters (keys = FILTER_KEYS, plus 'sort')."""
    g = lambda k: (f.get(k) or "").strip()
    return search(conn, now_iso, q=g("q"), route=g("route"), station=g("station"), area=g("area"), type_=g("type"),
                  status=g("status") or default_status, on_date=g("date"), limit=limit, tz=tz, agency=g("agency"),
                  operator=g("operator"), year=g("year"), ends=g("ends"), sort=g("sort") or "relevant")


def date_options(tz, days=7):
    """[(YYYY-MM-DD, label)] for today and the following days in zone tz."""
    today = datetime.now(ZoneInfo(tz)).date()
    out = []
    for i in range(days):
        d = today + timedelta(days=i)
        label = {0: "Today", 1: "Tomorrow"}.get(i, d.strftime("%A"))
        out.append((d.isoformat(), "%s (%s)" % (label, d.strftime("%d %b"))))
    return out


def _overlaps(r, d0, d1):
    return (r["valid_to"] is None or r["valid_to"] >= d0) and (r["valid_from"] is None or r["valid_from"] <= d1)


def facet_options(conn, now_iso, f, tz, default_status="current"):
    """For every dropdown: [{value, label, count, group}] computed from notices matching all OTHER filters.
    Zero-count options are dropped, except the currently selected value (so the UI never loses it)."""
    f = {k: (f.get(k) or "").strip() for k in FILTER_KEYS}
    f["status"] = f["status"] or default_status
    without = lambda k, **extra: search_f(conn, now_iso, {**f, k: "", "sort": "newest", **extra}, tz, limit=None,
                                          default_status="all" if k == "status" else default_status)
    stations_of = {}
    for nid, st in conn.execute("SELECT notice_id, station FROM notice_stations"):
        stations_of.setdefault(nid, []).append(st)

    def tally(rows, values_of):
        counts, group = {}, {}
        for r in rows:
            for v in values_of(r):
                counts[v] = counts.get(v, 0) + 1
                group.setdefault(v, r["agency"])
        return counts, group

    def build(key, counts, label=lambda v: v, group=None, order=None):
        sel = f[key]
        if sel and sel not in counts:
            counts[sel] = 0
        vals = order(counts) if order else sorted(counts, key=lambda v: ((group or {}).get(v, ""), label(v).lower()))
        no_group = "Your choice" if group is not None else None    # selected value that has no matches any more
        return [dict(value=v, label=label(v), count=counts[v], group=(group or {}).get(v, no_group))
                for v in vals if counts[v] or v == sel]

    out = {}
    c, _ = tally(without("agency"), lambda r: [r["agency"]])
    out["agency"] = build("agency", c)
    c, _ = tally(without("year"), lambda r: [(r["valid_from"] or r["published_at"] or r["observed_at"])[:4]])
    out["year"] = build("year", c, order=lambda cs: sorted(cs, reverse=True))
    c, _ = tally(without("ends"), lambda r: ["fixed" if r["valid_to"] else "open"])
    out["ends"] = build("ends", c, label=ENDS_LABELS.get, order=lambda cs: [k for k in ENDS_LABELS if k in cs])
    c, _ = tally(without("operator"), lambda r: [r["operator_type"]])
    out["operator"] = build("operator", c, label=lambda v: "%s operators" % v)
    c, g = tally(without("route"), lambda r: [x for x in r["route_id"].split(",") if x])
    out["route"] = build("route", c, group=g)
    c, g = tally(without("station"), lambda r: stations_of.get(r["notice_id"], []))
    out["station"] = build("station", c, group=g)
    c, _ = tally(without("area"), lambda r: [r["area"]] if r["area"] else [])
    out["area"] = build("area", c)
    c, _ = tally(without("type"), lambda r: [r["type"]])
    out["type"] = build("type", c, label=lambda v: TYPE_LABELS.get(v, v))
    rows = without("date")
    c = {}
    for v, _lbl in date_options(tz):
        d0, d1 = day_bounds(v, tz)
        c[v] = sum(1 for r in rows if _overlaps(r, d0, d1))
    labels = dict(date_options(tz))
    out["date"] = build("date", c, label=lambda v: labels.get(v, v), order=lambda cs: list(cs))
    rows = without("status", status="all")
    effs = [r["eff_status"] for r in rows]
    c = {k: (len(effs) if k == "all" else sum(1 for e in effs if e in STATUS_GROUPS.get(k, (k,))))
         for k, _ in SHOW_LABELS}
    out["status"] = build("status", c, label=dict(SHOW_LABELS).get, order=lambda cs: list(cs))
    return out


def facets(conn):
    """All values (no counts) for the alert sign-up tick boxes, grouped by agency."""
    def grouped(sql):
        res = {}
        for agency, value in conn.execute(sql):
            res.setdefault(agency, []).append(value)
        return res
    return dict(
        routes=grouped("""SELECT DISTINCT s.agency, r.route_id FROM notice_routes r JOIN notices n USING(notice_id)
                          JOIN sources s USING(source_id) WHERE s.enabled=1 ORDER BY 1, 2"""),
        stations=grouped("""SELECT DISTINCT s.agency, x.station FROM notice_stations x JOIN notices n USING(notice_id)
                            JOIN sources s USING(source_id) WHERE s.enabled=1 ORDER BY 1, 2"""),
        areas=[r[0] for r in conn.execute("""SELECT DISTINCT n.area FROM notices n JOIN sources s USING(source_id)
                                              WHERE s.enabled=1 AND n.area<>'' ORDER BY 1""")],
        types=[r[0] for r in conn.execute("""SELECT DISTINCT n.type FROM notices n JOIN sources s USING(source_id)
                                              WHERE s.enabled=1 ORDER BY 1""")],
    )


def stats(rows):
    by_type, by_route = {}, {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        for rt in [x for x in r["route_id"].split(",") if x]:
            by_route[rt] = by_route.get(rt, 0) + 1
    top = sorted(by_route.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return sorted(by_type.items(), key=lambda kv: -kv[1]), top
