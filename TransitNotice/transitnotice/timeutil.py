"""Time-zone policy (documented in docs/TIMEZONES.md):

* The database stores ONLY UTC, formatted YYYY-MM-DDTHH:MM:SSZ.
* Source timestamps that carry an offset are converted to UTC.
* Naive source timestamps ("2026-11-05 22:00") are interpreted in the source's configured IANA zone.
* Date-only values are start-of-day (valid_from) or end-of-day (valid_to) in the source zone.
* The UI renders in the viewer-selected zone (default: the source zone shown with its abbreviation).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FMT = "%Y-%m-%dT%H:%M:%SZ"
UTC = timezone.utc


def utcnow():
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(dt):
    return dt.astimezone(UTC).strftime(FMT)


def now_iso():
    return to_iso(utcnow())


def parse_dt(value, tz_name, end_of_day=False):
    """Parse many source formats to canonical UTC ISO. Returns None for empty/unparseable input."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "n/a", "tbd", "-", "ongoing", "until further notice"):
        return None
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for f in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d %b %Y %H:%M", "%d %B %Y %H:%M",
                  "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(s, f)
                if "%H" not in f and end_of_day:
                    dt = dt.replace(hour=23, minute=59, second=59)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if len(s) == 10 and end_of_day and dt.hour == 0 and dt.minute == 0:
        dt = dt.replace(hour=23, minute=59, second=59)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return to_iso(dt)


def from_iso(s):
    return datetime.strptime(s, FMT).replace(tzinfo=UTC) if s else None


def localize(iso, tz_name):
    """Render canonical UTC ISO in a zone: '05 Nov 2026 22:00 EST'."""
    if not iso:
        return "-"
    return from_iso(iso).astimezone(ZoneInfo(tz_name)).strftime("%d %b %Y %H:%M %Z")


def human_age(iso, now=None):
    if not iso:
        return "never"
    delta = (now or utcnow()) - from_iso(iso)
    secs = int(delta.total_seconds())
    if secs < 90:
        return "just now"
    if secs < 5400:
        return "%d min ago" % round(secs / 60)
    if secs < 172800:
        return "%d h ago" % round(secs / 3600)
    return "%d days ago" % (secs // 86400)


def add_hours(iso, hours):
    return to_iso(from_iso(iso) + timedelta(hours=hours))
