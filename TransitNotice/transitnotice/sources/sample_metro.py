"""Adapter: 'Sample Metro' HTML advisories page.

Sample Metro is a fictional agency whose notice page is served by this app from demo_source/ so that the
scraping path (HTTP -> HTML -> fields) can be demonstrated and changed live by a mentor without hitting a
real site. The HTML parser is tolerant: unclosed tags, missing fields and duplicates never crash a run.
"""
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..timeutil import parse_dt
from .base import FetchResult, ParseError, ParsedNotice
from .normalize import clean_text, normalize_type

FIELDS = {"title", "summary", "area", "type", "stations", "published", "from", "to"}
VOID = {"br", "img", "hr", "input", "meta", "link"}


class _NoticeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self.cur = [], None
        self.field = self.ftag = None
        self.depth = 0
        self.buf, self.attr_dt = [], None
        self.in_routes = False
        self.declared_empty = False

    def _close_field(self):
        if self.field and self.cur is not None:
            text = self.attr_dt or clean_text("".join(self.buf))
            if self.field == "route":
                if text:
                    self.cur["routes"].append(clean_text("".join(self.buf)))
            else:
                self.cur["fields"].setdefault(self.field, text)
        self.field = self.ftag = self.attr_dt = None
        self.buf, self.depth = [], 0

    def _flush(self):
        self._close_field()
        if self.cur is not None:
            self.items.append(self.cur)
        self.cur = None
        self.in_routes = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if a.get("data-empty") == "true":
            self.declared_empty = True
        if tag == "article" and "notice" in cls:
            self._flush()
            self.cur = {"id": (a.get("data-id") or "").strip(), "status": (a.get("data-status") or "active").lower(),
                        "routes": [], "fields": {}, "href": None}
            return
        if self.cur is None:
            return
        if self.field is not None:
            marker = any(c in FIELDS for c in cls) or (tag == "ul" and "routes" in cls) or (tag == "a" and "more" in cls)
            sibling_li = self.field == "route" and tag == "li"
            if marker or sibling_li:
                self._close_field()                    # unclosed tag: a new field starts, so the old one is over
            else:
                if tag == self.ftag and tag not in VOID:
                    self.depth += 1
                return
        if tag == "a" and "more" in cls and a.get("href"):
            self.cur["href"] = a["href"]
            return
        if tag == "ul" and "routes" in cls:
            self.in_routes = True
            return
        if tag == "li" and self.in_routes:
            self.field, self.ftag, self.depth, self.buf = "route", "li", 1, []
            return
        for c in cls:
            if c in FIELDS and tag not in VOID:
                self.field, self.ftag, self.depth, self.buf = c, tag, 1, []
                self.attr_dt = (a.get("datetime") or "").strip() or None
                return

    def handle_endtag(self, tag):
        if self.field is not None and tag == self.ftag:
            self.depth -= 1
            if self.depth <= 0:
                self._close_field()
            return
        if tag == "ul":
            if self.field == "route":
                self._close_field()
            self.in_routes = False
        elif tag == "article":
            self._flush()

    def handle_data(self, data):
        if self.field is not None:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def parse_page(html, page_url, tz):
    p = _NoticeHTML()
    p.feed(html)
    p.close()
    if not p.items and not p.declared_empty:
        raise ParseError("no notices and no empty-list marker: page layout changed or error page")
    out, skipped, diag, seen, unparsed = [], 0, [], set(), []
    for it in p.items:
        f = it["fields"]
        nid = it["id"]
        try:
            if not nid:
                raise ValueError("missing data-id")
            if nid in seen:
                raise ValueError("duplicate id %s on page" % nid)
            summary = f.get("summary") or ""
            title = f.get("title") or ""
            text = ("%s - %s" % (title, summary)) if title and summary else (summary or title)
            if not text:
                raise ValueError("no summary text")
            routes = list(dict.fromkeys(r for r in it["routes"] if r))
            area = f.get("area", "")
            if not routes and not area:
                raise ValueError("no route and no area")
            vf = parse_dt(f.get("from"), tz)
            vt = parse_dt(f.get("to"), tz, end_of_day=True)     # unparsable / 'TBD' -> open ended (None)
            if vf and vt and vt < vf:
                raise ValueError("valid_to before valid_from")
            status = it["status"] if it["status"] in ("cancelled", "withdrawn") else "active"
            seen.add(nid)
            out.append(ParsedNotice(
                external_id=nid, summary=text, type=normalize_type(f.get("type"), text), route_ids=routes,
                stations=[s.strip() for s in f.get("stations", "").replace(",", ";").split(";") if s.strip()],
                area=area, published_at=parse_dt(f.get("published"), tz), valid_from=vf, valid_to=vt,
                source_url=urljoin(page_url, it["href"]) if it["href"] else "%s#%s" % (page_url, nid),
                source_status=status))
        except ValueError as e:
            skipped += 1
            if nid:
                unparsed.append(nid)
            diag.append("skipped notice %r: %s" % (nid or "?", e))
    return out, skipped, diag, unparsed


class SampleMetroAdapter:
    source_key = "sample_metro"
    agency = "Sample Chennai Transit (demo agency)"
    timezone = "Asia/Kolkata"
    refresh_seconds = 300

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    @property
    def page_url(self):
        return self.base_url + "/demo-source/notices.html"

    def fetch(self, fetcher):
        status, body, _ = fetcher.get(self.page_url)
        notices, skipped, diag, unparsed = parse_page(body.decode("utf-8", "replace"), self.page_url, self.timezone)
        return FetchResult(notices, skipped, diag, status, unparsed)
