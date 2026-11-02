"""Adapter: Chennai Metro Rail Limited (CMRL) - Phase 2 traffic diversions, Chennai, Tamil Nadu.

Two steps, both on chennaimetrorail.org (robots.txt: "Disallow:" i.e. everything allowed):
  1. List  - the site's public WordPress REST endpoint /wp-json/wp/v2/road-diversion gives id, title, link,
             publish/modified time and corridor for every diversion notice.
  2. Detail - each notice's HTML page gives the diversion text, start date, CMRL package and "Last Updated On".
Detail pages are only downloaded again when the list says the post was modified (cached in data/), so a
normal refresh is 2 requests instead of ~45.
"""
import html
import json
from html.parser import HTMLParser
from pathlib import Path

from ..timeutil import parse_dt
from .base import FetchError, FetchResult, ParseError, ParsedNotice
from .normalize import clean_text

# Chennai Metro Phase 2 corridors (CMRL rd-category taxonomy slug -> description)
CORRIDORS = {
    "corridor-3": ("Corridor 3", "Madhavaram - SIPCOT"),
    "corridor-4": ("Corridor 4", "Light House - Poonamallee"),
    "corridor-5": ("Corridor 5", "Madhavaram - Sholinganallur"),
}


class _DiversionPage(HTMLParser):
    """Collects: h1.h1 title, <p>/<li> text inside div.description, info cards (h2.box-text -> p.box-title)
    and p.updated-date."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.divs = []                       # stack of class lists of currently open <div>s
        self.cap = self.cap_tag = None       # what we are capturing and the tag that ends it
        self.buf = []
        self.title, self.paras, self.cards, self.updated = "", [], {}, ""
        self._label = None

    def _in(self, cls):
        return any(cls in c for c in self.divs)

    def handle_starttag(self, tag, attrs):
        cls = (dict(attrs).get("class") or "").split()
        if tag == "div":
            self.divs.append(cls)
        if self.cap:
            return
        if tag == "h1" and "h1" in cls:
            self.cap = "title"
        elif tag == "h2" and "box-text" in cls:
            self.cap = "label"
        elif tag == "p" and "box-title" in cls:
            self.cap = "value"
        elif tag == "p" and "updated-date" in cls:
            self.cap = "updated"
        elif tag in ("p", "li") and self._in("description"):
            self.cap = "para"
        if self.cap:
            self.cap_tag, self.buf = tag, []

    def handle_endtag(self, tag):
        if self.cap and tag == self.cap_tag:
            text = clean_text("".join(self.buf))
            if self.cap == "title":
                self.title = text
            elif self.cap == "label":
                self._label = text
            elif self.cap == "value" and self._label:
                self.cards[self._label] = text
            elif self.cap == "updated":
                self.updated = text.split(":", 1)[-1].strip()
            elif self.cap == "para" and text:
                self.paras.append(text)
            self.cap = self.cap_tag = None
        if tag == "div" and self.divs:
            self.divs.pop()

    def handle_data(self, data):
        if self.cap:
            self.buf.append(data)


def parse_detail(page_html):
    """Returns dict(title, description, start, package, updated). Raises ParseError if it is not a diversion page."""
    p = _DiversionPage()
    p.feed(page_html)
    p.close()
    if not p.title or "Diversion Start Date" not in p.cards:
        raise ParseError("not a diversion page (title or start-date card missing)")
    return dict(title=p.title, description=" ".join(p.paras), start=p.cards.get("Diversion Start Date", ""),
                package=p.cards.get("CMRL Package", ""), updated=p.updated)


def build_notice(item, detail, corridor_slug, tz):
    """Combine one REST list item + parsed detail page into a ParsedNotice."""
    location = clean_text(html.unescape(item["title"]["rendered"]))
    route, corridor_name = CORRIDORS.get(corridor_slug, (corridor_slug or "Unknown corridor", ""))
    area = "Chennai - %s (%s)" % (route, corridor_name) if corridor_name else "Chennai"
    text = detail["description"] or "Traffic diversion for metro construction work."
    summary = "%s: %s" % (detail["title"] or location + " Traffic Diversion", text)
    if detail["package"]:
        summary += " (CMRL package %s)" % detail["package"]
    return ParsedNotice(
        external_id=str(item["id"]), summary=summary, type="detour", route_ids=[route], stations=[location],
        area=area, published_at=parse_dt(item.get("date_gmt"), "UTC"),
        valid_from=parse_dt(detail["start"], tz),
        valid_to=None,                       # CMRL does not announce end dates: open-ended until removed
        source_url=item["link"], source_status="active")


class CmrlAdapter:
    source_key = "cmrl"
    agency = "Chennai Metro Rail Limited (CMRL)"
    label = "CMRL road diversions"
    timezone = "Asia/Kolkata"
    refresh_seconds = 6 * 3600              # diversions change rarely; be gentle with a public site
    base_url = "https://chennaimetrorail.org"
    timeout = 30                            # the site can be slow; still bounded
    def __init__(self, cache_path=None):
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache = self._load_cache()

    def _load_cache(self):
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8")) if self.cache_path else {}
        except (OSError, ValueError):
            return {}

    def _save_cache(self):
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")

    def _list(self, fetcher):
        items, page, pages = [], 1, 1
        while page <= pages and page <= 10:
            status, body, headers = fetcher.get(
                "%s/wp-json/wp/v2/road-diversion?per_page=100&page=%d&_fields=id,slug,link,title,date_gmt,"
                "modified_gmt,rd-category" % (self.base_url, page), timeout=self.timeout)
            try:
                data = json.loads(body)
            except ValueError as e:
                raise ParseError("diversion list is not JSON: %s" % e) from e
            if not isinstance(data, list):
                raise ParseError("diversion list is not a JSON array")
            items += data
            hdr = {k.lower(): v for k, v in (headers or {}).items()}
            pages = int(hdr.get("x-wp-totalpages", "1") or 1)
            page += 1
        return status, items

    def _corridors(self, fetcher):
        _, body, _ = fetcher.get("%s/wp-json/wp/v2/rd-category?per_page=100&_fields=id,slug" % self.base_url,
                                 timeout=self.timeout)
        return {c["id"]: c["slug"] for c in json.loads(body)}

    def fetch(self, fetcher):
        status, items = self._list(fetcher)
        if not items:
            raise ParseError("diversion list is empty - refusing to treat that as 'all withdrawn'")
        corridor_of = self._corridors(fetcher)
        out, skipped, diag, unparsed = [], 0, [], []
        for it in items:
            key = str(it.get("id"))
            try:
                cached = self.cache.get(key)
                if cached and cached["modified"] == it.get("modified_gmt"):
                    detail = cached["detail"]
                else:
                    _, body, _ = fetcher.get(it["link"], timeout=self.timeout)
                    detail = parse_detail(body.decode("utf-8", "replace"))
                    self.cache[key] = {"modified": it.get("modified_gmt"), "detail": detail}
                    self._save_cache()                   # keep progress even if a later page fails
                cats = it.get("rd-category") or []
                out.append(build_notice(it, detail, corridor_of.get(cats[0]) if cats else None, self.timezone))
            except (FetchError, ParseError, KeyError, TypeError, ValueError) as e:
                # One slow/broken detail page must not sink the whole run. The item is still listed by CMRL,
                # so it goes to unparsed_ids: never withdrawn, and its last_confirmed_at is not bumped.
                skipped += 1
                unparsed.append(key)
                diag.append("skipped diversion %s: %s" % (key, e))
        return FetchResult(out, skipped, diag, status, unparsed)
