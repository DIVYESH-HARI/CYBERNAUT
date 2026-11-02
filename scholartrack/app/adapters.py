"""Source adapters: one per approved notice LAYOUT. Selectors/labels can be overridden in sources.json.

Layouts:
  table         rows of a <table>, columns named by <th>          (demo portal A)
  cards         repeated cards with a heading link (+ optional <dl>) (demo portal B, Erasmus Mundus catalogue)
  blocks        repeated blocks with "Label : value" badges        (National Scholarship Portal)
  detail_pages  an index page linking to one official page per scholarship; the deadline is read from
                each page by a configured rule                      (CSC UK, USIEF Fulbright-Nehru)
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from .fetcher import FetchError

ALIASES = {
    "title": {"scholarship", "title", "name", "notice", "programme name", "program name", "fellowship", "award"},
    "provider": {"provider", "funder", "sponsor", "organisation", "organization", "offered by", "awarding body"},
    "education_text": {"level", "study level", "education level", "degree", "for"},
    "discipline": {"discipline", "field", "subject", "programme", "program", "area", "field of study"},
    "region": {"region", "country", "location", "open to", "eligible region"},
    "residency_rule": {"residency", "residency rule", "citizenship", "domicile"},
    "funding_text": {"category", "funding", "funding type", "type", "funding category"},
    "benefit_text": {"benefit", "benefits", "award value", "value", "amount", "funding amount"},
    "eligibility_excerpt": {"eligibility", "who can apply", "criteria", "requirements"},
    "due_text": {"deadline", "due", "due date", "closing date", "apply by", "last date", "closes"},
    "status_text": {"status", "notice status"},
}
LABEL_TO_FIELD = {alias: fld for fld, names in ALIASES.items() for alias in names}


@dataclass
class RawNotice:
    external_key: str
    url: str
    title: str
    fields: dict = field(default_factory=dict)   # provider, education_text, discipline, ... due_text, status_text
    extra_status: str = ""                       # css classes/badges that hint at status


@dataclass
class ParseResult:
    notices: list[RawNotice]
    warnings: list[str]


@dataclass
class CollectResult:
    """Everything one refresh collected. If `complete` is False some pages could not be read, so the
    pipeline must NOT conclude that missing notices were removed by the source."""
    notices: list[RawNotice]
    warnings: list[str]
    url: str
    http_status: int | None = None
    attempts: int = 0
    complete: bool = True
    skipped_keys: set = field(default_factory=set)


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _absolute(href: str | None, base: str) -> str | None:
    if not href or href.startswith(("javascript:", "mailto:", "#")) or href.strip().lower() in ("null", "undefined"):
        return None
    return urldefrag(urljoin(base, href))[0]


def _label_map(config: dict) -> dict:
    extra = {k.lower(): v for k, v in (config.get("labels") or {}).items()}
    return {**LABEL_TO_FIELD, **extra}


class Adapter:
    def parse(self, html: str, base_url: str, config: dict) -> ParseResult:
        raise NotImplementedError

    def collect(self, fetcher, listing_url: str, config: dict) -> CollectResult:
        """Fetch the listing page, follow `next_selector` pagination up to `max_pages`, parse each page."""
        max_pages = int(config.get("max_pages", 1))
        out = CollectResult([], [], listing_url)
        seen_pages, keys, url = set(), set(), listing_url
        for page_no in range(1, max_pages + 1):
            try:
                res = fetcher.get(url)
            except FetchError as exc:
                if page_no == 1:
                    raise
                out.warnings.append(f"page {page_no} failed ({exc.kind}); later pages not read")
                out.complete = False
                break
            seen_pages.add(url)
            out.http_status, out.attempts = res.status, out.attempts + res.attempts
            parsed = self.parse(res.text, res.url, config)
            out.warnings += [f"page {page_no}: {w}" if page_no > 1 else w for w in parsed.warnings]
            for n in parsed.notices:
                if n.external_key not in keys:
                    keys.add(n.external_key)
                    out.notices.append(n)
            nxt = BeautifulSoup(res.text, "lxml").select_one(config["next_selector"]) if config.get("next_selector") else None
            next_url = _absolute(nxt.get("href"), res.url) if nxt else None
            if not next_url or next_url in seen_pages:
                break
            if page_no == max_pages:
                out.warnings.append(f"stopped after max_pages={max_pages}; more pages exist")
                out.complete = False
            url = next_url
        return out


class TableAdapter(Adapter):
    """Layout A: a <table> where each <tr> is a notice and <th> headers name the columns."""

    def parse(self, html: str, base_url: str, config: dict) -> ParseResult:
        soup = BeautifulSoup(html, "lxml")
        labels = _label_map(config)
        table = soup.select_one(config.get("table", "table.notices")) or soup.find("table")
        if table is None:
            return ParseResult([], ["no notice table found - layout may have changed"])
        header_cells = table.select("thead th") or (table.find("tr").find_all("th") if table.find("tr") else [])
        columns = [labels.get(_text(th).lower()) for th in header_cells]
        if "title" not in columns:
            return ParseResult([], ["table has no recognisable title column - layout may have changed"])

        notices, warnings, seen = [], [], set()
        for n, tr in enumerate(table.select("tbody tr") or table.find_all("tr")[1:], 1):
            cells = tr.find_all("td")
            if not cells:
                continue
            row = {columns[i]: _text(c) for i, c in enumerate(cells) if i < len(columns) and columns[i]}
            title = row.get("title", "")
            title_cell = cells[columns.index("title")] if columns.index("title") < len(cells) else None
            link = title_cell.find("a", href=True) if title_cell else None
            link = link or tr.find("a", href=True)
            url = _absolute(link["href"], base_url) if link else None
            if not title or not url:
                warnings.append(f"row {n} skipped: missing {'title' if not title else 'link'}")
                continue
            key = tr.get("data-id") or url
            if key in seen:
                warnings.append(f"row {n} skipped: duplicate of {key}")
                continue
            seen.add(key)
            fields = {k: v for k, v in row.items() if k != "title"}
            notices.append(RawNotice(key, url, title, fields, " ".join(tr.get("class", []))))
        return ParseResult(notices, warnings)


class CardAdapter(Adapter):
    """Layout B: repeated cards (default <article class="notice">) with a heading link and optional <dl> fields."""

    def parse(self, html: str, base_url: str, config: dict) -> ParseResult:
        soup = BeautifulSoup(html, "lxml")
        labels = _label_map(config)
        cards = soup.select(config.get("card", "article.notice"))
        if not cards:
            return ParseResult([], ["no notice cards found - layout may have changed"])

        notices, warnings, seen = [], [], set()
        for n, card in enumerate(cards, 1):
            heading = card.select_one(config.get("title", "h2, h3"))
            title = _text(heading)
            link = card.select_one("a[data-official]") or (heading.find("a", href=True) if heading else None)
            url = _absolute(link.get("href") if link else None, base_url)
            key = card.get("data-id") or url
            if title and not url and config.get("fallback_to_listing"):
                # The source lists the item without its own link: point at the official listing page instead.
                url, key = base_url, card.get("data-id") or f"title:{title}"
            if not title or not url:
                warnings.append(f"card {n} skipped: missing {'title' if not title else 'link'}")
                continue
            if key in seen:
                warnings.append(f"card {n} skipped: duplicate of {key}")
                continue
            seen.add(key)
            fields = {}
            for dt in card.select("dl dt"):
                dd = dt.find_next_sibling("dd")
                target = labels.get(_text(dt).lower().rstrip(":"))
                if target and dd is not None and target != "title":
                    fields[target] = _text(dd)
            badges = " ".join(_text(b) for b in card.select(".badge, .status")) + " " + " ".join(card.get("class", []))
            notices.append(RawNotice(key, url, title, fields, badges))
        return ParseResult(notices, warnings)


class BlockAdapter(Adapter):
    """Layout C: repeated blocks holding a title and "Label : value" badges, grouped under a heading
    (e.g. one ministry with several schemes). `badge_labels` maps label prefixes to fields; the first
    matching label wins, so list the preferred label first."""

    def parse(self, html: str, base_url: str, config: dict) -> ParseResult:
        soup = BeautifulSoup(html, "lxml")
        blocks = soup.select(config.get("block", ".notice-block"))
        if not blocks:
            return ParseResult([], ["no notice blocks found - layout may have changed"])
        badge_labels = [(k.lower(), v) for k, v in (config.get("badge_labels") or {}).items()]
        link_text = re.compile(config.get("link_text", "."), re.I)

        notices, warnings, seen = [], [], set()
        for n, block in enumerate(blocks, 1):
            title = _text(block.select_one(config.get("title", "h6, h5, h4")))
            if not title:
                warnings.append(f"block {n} skipped: missing title")
                continue
            group = block.find_parent(class_=config["group_class"]) if config.get("group_class") else None
            group_name = _text(group.select_one(config.get("group_title", "button"))) if group else ""
            link = next((a for a in block.find_all("a", href=True) if link_text.search(_text(a))), None)
            url = (_absolute(link["href"], base_url) if link else None) or base_url
            key = f"{group_name}|{title}"
            if key in seen:
                warnings.append(f"block {n} skipped: duplicate of {title}")
                continue
            seen.add(key)
            fields, notes = {}, []
            if group_name:
                fields["provider"] = group_name
            for badge in block.select(config.get("badge", "span")):
                text = _text(badge)
                if ":" not in text:
                    continue
                label, value = (part.strip() for part in text.split(":", 1))
                label = " ".join(label.lower().split())
                for prefix, target in badge_labels:
                    if label == prefix and target not in fields:
                        fields[target] = value
                        if "renewal" in prefix:
                            notes.append("Open for renewal applications only")
                        break
            if notes:
                fields["eligibility_excerpt"] = "; ".join(notes)
            notices.append(RawNotice(key, url, title, fields, ""))
        return ParseResult(notices, warnings)


class DetailPagesAdapter(Adapter):
    """Layout D: an index page links to one official page per scholarship. Each page is fetched (politely,
    through the same Fetcher) and the deadline is read with configured rules:
      {"type": "sentence", "pattern": "closing date for applications is (?P<when>[^.]+)"}
      {"type": "table_label", "label": "application deadline"}   -> the other cell of that table row
    A page that cannot be fetched is reported and its notice is kept as it was (not marked removed)."""

    def parse(self, html: str, base_url: str, config: dict) -> ParseResult:
        """Parses the INDEX page into (title, url) stubs."""
        soup = BeautifulSoup(html, "lxml")
        scope = soup.select_one(config.get("link_scope", "main")) or soup
        pattern = re.compile(config["link_pattern"])
        exclude = re.compile(config["exclude_pattern"]) if config.get("exclude_pattern") else None
        strip = re.compile(config["title_strip"], re.I) if config.get("title_strip") else None
        best: dict[str, str] = {}
        for a in scope.find_all("a", href=True):
            url = _absolute(a["href"], base_url)
            title = _text(a)
            if not url or not title or not pattern.search(url) or (exclude and exclude.search(url)):
                continue
            if strip:
                title = strip.sub("", title).strip(" -–—")
            if len(title) > len(best.get(url, "")):      # the same page is often linked twice; keep the fuller name
                best[url] = title
        notices = [RawNotice(url, url, title, {}) for url, title in best.items()]
        warnings = [] if notices else ["no scholarship links found on the index page - layout may have changed"]
        return ParseResult(notices[: int(config.get("max_details", 30))], warnings)

    def collect(self, fetcher, listing_url: str, config: dict) -> CollectResult:
        index = super().collect(fetcher, listing_url, {**config, "max_pages": 1})
        for stub in index.notices:
            try:
                page = fetcher.get(stub.url)
            except FetchError as exc:
                index.warnings.append(f"could not read {stub.url} ({exc.kind}); kept previous data")
                index.skipped_keys.add(stub.external_key)
                continue
            index.attempts += page.attempts
            stub.fields.update(self._details(page.text, config))
        index.notices = [n for n in index.notices if n.external_key not in index.skipped_keys]
        return index

    @staticmethod
    def _details(html: str, config: dict) -> dict:
        soup = BeautifulSoup(html, "lxml")
        # Only drop code. Some sites wrap their whole article in <header>, so layout tags are kept.
        for junk in soup(["script", "style", "noscript"]):
            junk.decompose()
        fields = {}
        meta = soup.find("meta", attrs={"name": "description"})
        summary = meta.get("content", "").strip() if meta else ""
        if summary:
            # Used to classify level/subject; only stored as an excerpt when the source allows it.
            fields["summary_text"] = summary
            if config.get("excerpt_from_meta"):
                fields["eligibility_excerpt"] = summary
        for rule in config.get("deadline_rules", []):
            if rule["type"] == "sentence":
                text = _text(soup.find("main") or soup.body)
                m = re.search(rule["pattern"], text, re.I)
                if m:
                    fields["due_text"] = m.group("when").strip()
            elif rule["type"] == "table_label":
                label = rule["label"].lower()
                for tr in soup.find_all("tr"):
                    cells = [_text(td) for td in tr.find_all(["td", "th"])]
                    if any(c.lower() == label for c in cells):
                        others = [c for c in cells if c.lower() != label and c]
                        if others:
                            fields["due_text"] = others[0]
                            break
            if "due_text" in fields:
                break
        return fields


ADAPTERS = {"table": TableAdapter(), "cards": CardAdapter(), "blocks": BlockAdapter(), "detail_pages": DetailPagesAdapter()}
