"""Adapter: Chennai Metro Rail Limited (CMRL) press releases - passenger service announcements.

https://chennaimetrorail.org/press-release/ lists every press release (2008 onwards) in year tables:
Date | Release title | PDF link. One page request per refresh. robots.txt allows everything.

Only passenger-facing releases are kept (closures, timing/frequency changes, maintenance, special services,
parking, ticketing ...). Corporate news (contracts, ridership figures, construction milestones) is skipped.

Validity window (press releases do not carry one, so this is a documented heuristic):
  * dates written in the title ("between 20.10.2025 and 24.10.2025", "on 5th January 2025",
    "January 14, 15 and 16") -> from the first to the last date mentioned;
  * "Permanent ..." in the title -> open ended from the release date;
  * otherwise -> current for ANNOUNCEMENT_DAYS after the release date, then it moves to 'Ended'.
"""
import hashlib
import html
import json
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from ..timeutil import parse_dt, to_iso
from .base import FetchResult, ParseError, ParsedNotice
from .normalize import clean_text

ANNOUNCEMENT_DAYS = 14

PASSENGER = re.compile(
    r"servic|timing|time ?table|frequen|train|clos|divers|maintenance|track|extend|additional|special|"
    r"holiday|sunday|festival|deepavali|diwali|pongal|parking|entry|entrance|exit|ticket|\bqr\b|fare|card|"
    r"pass\b|metro connect|feeder|lift|escalator|suspend|disrupt|rain|flood|cyclone|match|ipl|concert|marathon|"
    r"open(ed|ing)? |commenc|discontinu|change", re.I)
CORPORATE = re.compile(
    r"contract|agreement|award|milestone|breakthrough|\btbm\b|inaugurat|celebrat|recogni|passengers (have )?"
    r"(recorded|travelled)|ridership|signed|signs|consultan|procure|letter of acceptance|takes over|charge as|"
    r"\bmou\b|approv|bidder|tender|symposium|awareness|training cent|loan|jica|board meeting|visit|completion|"
    r"certificat|trials?\b|testing|overhaul|safety audit", re.I)

KINDS = [  # first match wins
    ("station_closure", re.compile(r"\bclos(e|ed|es|ing|ure)\b|\bshut( ?down)?\b", re.I)),   # not "shuttle"
    ("construction", re.compile(r"maintenance|track|construction work", re.I)),
    ("detour", re.compile(r"divers|detour", re.I)),
    ("accessibility", re.compile(r"lift|escalator|accessib|disabilit", re.I)),
    ("suspension", re.compile(r"suspend|no service|not operat|disrupt", re.I)),
    ("other", re.compile(r"amenit|facilit|parking|ticket|card|fare|\bqr\b|fine|penalt|\bapp\b|whatsapp|paytm", re.I)),
    ("schedule_change", re.compile(r"timing|time ?table|frequen|extend|additional|special|holiday|sunday|"
                                   r"schedul|service|train|match|ipl|concert|marathon|festival|deepavali", re.I)),
]

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
DOTTED = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(%s)[a-z]*\.?,?\s*(\d{4})?" % MONTHS, re.I)
MONTH_DAYS = re.compile(r"\b(%s)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b((?:\s*(?:,|and|&)\s*\d{1,2}(?:st|nd|rd|th)?\b)*)"
                        r"(?:,?\s*(\d{4}))?" % MONTHS, re.I)


def _month(m):
    return "jan feb mar apr may jun jul aug sep oct nov dec".split().index(m[:3].lower()) + 1


def title_dates(title, released):
    """All calendar dates mentioned in a title, as datetime.date objects. `released` is the release date;
    a date written without a year is taken as the next such date on/after (release - 31 days)."""
    out = []

    def add(y, m, d):
        try:
            if y:
                out.append(datetime(int(y), m, int(d)).date())
                return
            dt = datetime(released.year, m, int(d)).date()
            if dt < released - timedelta(days=31):
                dt = dt.replace(year=dt.year + 1)
            out.append(dt)
        except ValueError:
            pass

    for d, m, y in DOTTED.findall(title):
        add(y, int(m), d)
    for d, mon, y in DAY_MONTH.findall(title):
        add(y, _month(mon), d)
    for mon, d, more, y in MONTH_DAYS.findall(title):
        for day in [d] + re.findall(r"\d{1,2}", more):
            add(y, _month(mon), day)
    return sorted(set(out))


def classify(title):
    """Returns the notice type, or None when the release is not a passenger-service announcement."""
    if CORPORATE.search(title) or not PASSENGER.search(title):
        return None
    for kind, rx in KINDS:
        if rx.search(title):
            return kind
    return "other"


def lines_in(title):
    found = []
    if re.search(r"blue\s*line", title, re.I):
        found.append("Blue Line")
    if re.search(r"green\s*line", title, re.I):
        found.append("Green Line")
    for n in re.findall(r"corridor[\s-]*(\d)", title, re.I):
        found.append("Corridor %s" % n)
    return list(dict.fromkeys(found)) or ["All Chennai Metro lines"]


class _PressTable(HTMLParser):
    """Collects (date, title, href) from every <tr> with at least two <td> cells."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cells, self.href = [], None, None
        self.in_td, self.buf = False, []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells, self.href = [], None
        elif tag == "td" and self.cells is not None:
            self.in_td, self.buf = True, []
        elif tag == "a" and self.cells is not None and self.href is None:
            self.href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "td" and self.in_td:
            self.cells.append(clean_text("".join(self.buf)))
            self.in_td = False
        elif tag == "tr" and self.cells is not None:
            if len(self.cells) >= 2:
                self.rows.append((self.cells[0], self.cells[1], self.href))
            self.cells = None

    def handle_data(self, data):
        if self.in_td:
            self.buf.append(data)


def parse_release_date(s, tz):
    s = s.strip().replace(".", "/").replace("-", "/")
    return parse_dt(s, tz)                 # dd/mm/yyyy in the agency's zone


def station_patterns(stations):
    """stations: {official name: line}. Matches the full name and, for long official names, a short alias
    (last word, e.g. 'Puratchi Thalaivar Dr. M.G.Ramachandran Central' -> 'Central')."""
    pats = []
    for name, line in stations.items():
        words = name.replace(".", " ").split()
        aliases = {name}
        if len(words) >= 3 and len(words[-1]) >= 4:
            aliases.add(words[-1])
        for a in aliases:
            if len(a) >= 4:
                pats.append((name, line, re.compile(r"\b%s\b" % re.escape(a), re.I)))
    return pats


def parse_press_page(page_html, page_url, tz, stations=None):
    """stations: optional {station name: line name} used to tag stations and lines mentioned in titles."""
    p = _PressTable()
    p.feed(page_html)
    p.close()
    if len(p.rows) < 10:
        raise ParseError("press-release table not found (only %d rows) - layout changed?" % len(p.rows))
    zone = ZoneInfo(tz)
    out, skipped, diag, seen = [], 0, [], set()
    station_rx = station_patterns(stations or {})
    for date_s, title, href in p.rows:
        title = clean_text(html.unescape(title))
        kind = classify(title)
        if kind is None:
            continue                                         # corporate news: not a service notice
        released = parse_release_date(date_s, tz)
        if not released:
            skipped += 1
            diag.append("skipped release with unreadable date %r" % date_s)
            continue
        rel_local = datetime.strptime(released, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
        link = urljoin(page_url, href) if href and href != "#" else page_url
        ext = (href.rsplit("/", 1)[-1] if href and href != "#" else
               hashlib.sha1(("%s|%s" % (date_s, title)).encode()).hexdigest()[:12])
        if ext in seen:
            ext = "%s-%s" % (ext, hashlib.sha1(title.encode()).hexdigest()[:6])
        seen.add(ext)
        dates = title_dates(title, rel_local.date())
        if dates:
            vf = to_iso(datetime(dates[0].year, dates[0].month, dates[0].day, tzinfo=zone))
            vt = to_iso(datetime(dates[-1].year, dates[-1].month, dates[-1].day, 23, 59, 59, tzinfo=zone))
            if vf > released and (datetime.fromisoformat(vf[:-1]) - datetime.fromisoformat(released[:-1])).days > 400:
                vf = released                                # implausible year guess; fall back
        elif re.search(r"permanent", title, re.I):
            vf, vt = released, None
        else:
            vf = released
            vt = to_iso(rel_local.replace(hour=23, minute=59, second=59) + timedelta(days=ANNOUNCEMENT_DAYS))
        if vt and vt < vf:
            vf, vt = released, to_iso(rel_local + timedelta(days=ANNOUNCEMENT_DAYS))
        found = [(name, line) for name, line, rx in station_rx if rx.search(title)]
        found_st = list(dict.fromkeys(name for name, _ in found))
        routes = [r for r in lines_in(title) if r != "All Chennai Metro lines"]
        routes = list(dict.fromkeys(routes + [line for _, line in found if line])) or ["All Chennai Metro lines"]
        area = ("Chennai - %s" % routes[0]) if len(routes) == 1 and routes[0] != "All Chennai Metro lines" \
            else "Chennai Metro network"
        out.append(ParsedNotice(
            external_id=ext, summary="%s (CMRL press release)" % title, type=kind, route_ids=routes, stations=found_st,
            area=area, published_at=released, valid_from=vf, valid_to=vt, source_url=link, source_status="active"))
    return out, skipped, diag


class CmrlPressAdapter:
    source_key = "cmrl_press"
    agency = "Chennai Metro Rail Limited (CMRL)"
    label = "CMRL press releases"
    timezone = "Asia/Kolkata"
    refresh_seconds = 6 * 3600
    base_url = "https://chennaimetrorail.org"
    timeout = 30

    @property
    def page_url(self):
        return self.base_url + "/press-release/"

    def _stations(self, fetcher):
        """{station name: line} from CMRL's station-information list (best effort; tagging is optional)."""
        try:
            _, body, _ = fetcher.get(self.base_url + "/wp-json/wp/v2/station-information?per_page=100"
                                     "&_fields=title,class_list", timeout=self.timeout)
            out = {}
            for s in json.loads(body):
                name = re.sub(r"\s+Metro$", "", clean_text(html.unescape(s["title"]["rendered"])))
                cls = " ".join(s.get("class_list") or [])
                out[name] = "Blue Line" if "blue-line" in cls else "Green Line" if "green-line" in cls else ""
            return out
        except Exception:  # noqa: BLE001
            return {}

    def fetch(self, fetcher):
        stations = self._stations(fetcher)
        status, body, _ = fetcher.get(self.page_url, timeout=self.timeout)
        notices, skipped, diag = parse_press_page(body.decode("utf-8", "replace"), self.page_url, self.timezone, stations)
        return FetchResult(notices, skipped, diag, status)
