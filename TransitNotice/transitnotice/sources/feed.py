"""Adapter: any RSS 2.0 / Atom feed of service notices (configured in agencies.json, no code needed).

Mapping:
  id            <guid> / Atom <id> (falls back to <link>)
  routes        each <category> / Atom <category term>
  summary       title + " - " + description (HTML stripped)
  type          keyword-normalised from title + description
  window        dates found in the text: "from <date> to <date>", "on <date>", "until <date>";
                otherwise valid from the publish date, open ended
  cancelled     title starting with "Cancelled:" / "Withdrawn:"
"""
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin

from ..timeutil import parse_dt, to_iso
from .base import FetchResult, ParseError, ParsedNotice
from .normalize import clean_text, normalize_type

ATOM = "{http://www.w3.org/2005/Atom}"
DATE = r"(\d{1,2} [A-Za-z]{3,9},? \d{4}(?:,? \d{1,2}:\d{2})?|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)"
RANGE = re.compile(r"(?:from|between)\s+" + DATE + r"\s+(?:to|and|till|until)\s+" + DATE, re.I)
FROM = re.compile(r"\b(?:from|starting|with effect from|w\.e\.f\.?)\s+" + DATE, re.I)
ON = re.compile(r"\bon\s+" + DATE, re.I)
UNTIL = re.compile(r"\b(?:until|till)\s+" + DATE, re.I)


def _strip_html(s):
    return clean_text(unescape(re.sub(r"<[^>]+>", " ", s or "")))


def _d(s):
    return (s or "").replace(",", "")


def find_window(text, tz):
    """Best-effort (valid_from, valid_to) from free text; (None, None) if no date is mentioned."""
    m = RANGE.search(text)
    if m:
        return parse_dt(_d(m.group(1)), tz), parse_dt(_d(m.group(2)), tz, end_of_day=True)
    m = ON.search(text)
    if m:
        return parse_dt(_d(m.group(1)), tz), parse_dt(_d(m.group(1)), tz, end_of_day=True)
    start = FROM.search(text)
    end = UNTIL.search(text)
    return (parse_dt(_d(start.group(1)), tz) if start else None,
            parse_dt(_d(end.group(1)), tz, end_of_day=True) if end else None)


def _pubdate(s):
    if not s:
        return None
    try:
        return to_iso(parsedate_to_datetime(s.strip()))      # RSS: RFC 822
    except (TypeError, ValueError):
        return parse_dt(s, "UTC")                              # Atom: ISO 8601


def parse_feed(body, tz, area="", type_hint="", base_url=""):
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ParseError("feed is not valid XML: %s" % e) from e
    if root.tag == "rss":
        entries = [dict(id=i.findtext("guid") or i.findtext("link"), title=i.findtext("title"),
                        text=i.findtext("description"), link=i.findtext("link"), date=i.findtext("pubDate"),
                        cats=[c.text for c in i.findall("category") if c.text]) for i in root.iter("item")]
    elif root.tag == ATOM + "feed":
        entries = []
        for e in root.iter(ATOM + "entry"):
            link = e.find(ATOM + "link")
            entries.append(dict(id=e.findtext(ATOM + "id"), title=e.findtext(ATOM + "title"),
                                text=e.findtext(ATOM + "summary") or e.findtext(ATOM + "content"),
                                link=link.get("href") if link is not None else "",
                                date=e.findtext(ATOM + "updated") or e.findtext(ATOM + "published"),
                                cats=[c.get("term") for c in e.findall(ATOM + "category") if c.get("term")]))
    else:
        raise ParseError("not an RSS or Atom feed (root element %s)" % root.tag)
    out, skipped, diag, unparsed, seen = [], 0, [], [], set()
    for e in entries:
        eid = clean_text(e["id"] or "")
        try:
            title, text = _strip_html(e["title"]), _strip_html(e["text"])
            if not eid:
                raise ValueError("no guid/id/link")
            if eid in seen:
                raise ValueError("duplicate id")
            if not (title or text):
                raise ValueError("no title or text")
            low = title.lower()
            status = "cancelled" if low.startswith(("cancelled:", "canceled:")) else \
                     "withdrawn" if low.startswith("withdrawn:") else "active"
            published = _pubdate(e["date"])
            vf, vt = find_window(title + " " + text, tz)
            if vf and vt and vt < vf:
                raise ValueError("end before start")
            seen.add(eid)
            out.append(ParsedNotice(
                external_id=eid, summary=" - ".join(x for x in (title, text) if x),
                type=normalize_type(type_hint, title + " " + text), route_ids=[clean_text(c) for c in e["cats"]],
                area=area, published_at=published, valid_from=vf or published, valid_to=vt,
                source_url=urljoin(base_url, e["link"] or "") or base_url, source_status=status))
        except ValueError as err:
            skipped += 1
            if eid:
                unparsed.append(eid)
            diag.append("skipped feed entry %r: %s" % (eid or "?", err))
    return out, skipped, diag, unparsed


class FeedAdapter:
    def __init__(self, key, agency, url, operator_type="Government", timezone="Asia/Kolkata",
                 refresh_seconds=1800, area="", type_hint=""):
        self.source_key, self.agency, self.base_url = key, agency, url
        self.operator_type, self.timezone, self.refresh_seconds = operator_type, timezone, refresh_seconds
        self.area, self.type_hint = area, type_hint

    def fetch(self, fetcher):
        status, body, _ = fetcher.get(self.base_url)
        notices, skipped, diag, unparsed = parse_feed(body, self.timezone, self.area, self.type_hint, self.base_url)
        return FetchResult(notices, skipped, diag, status, unparsed)
