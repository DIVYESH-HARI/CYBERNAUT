"""Builds docs/report.pdf (8 pages, A4) for the TransitNotice WEB-07 project.

    python docs/build_report.py

Screenshots are read from docs/img/ (captured from the running app).
"""
from pathlib import Path

from PIL import Image as PILImage
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
OUT = ROOT / "docs" / "report.pdf"

# ---------------------------------------------------------------- fonts & colours
FONT, BOLD = "Helvetica", "Helvetica-Bold"
for name, file in (("Arial", "arial.ttf"), ("Arial-Bold", "arialbd.ttf")):
    path = Path("C:/Windows/Fonts") / file
    if path.exists():
        pdfmetrics.registerFont(TTFont(name, str(path)))
if "Arial" in pdfmetrics.getRegisteredFontNames():
    FONT, BOLD = "Arial", "Arial-Bold"
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial", boldItalic="Arial-Bold")

NAVY = colors.HexColor("#031a29")
TEAL = colors.HexColor("#0a6f8f")
LIGHT = colors.HexColor("#e8f3f7")
GREY = colors.HexColor("#5b6b78")
LINE = colors.HexColor("#c9d6df")
GREEN = colors.HexColor("#17713a")

W = A4[0] - 3.6 * cm          # usable width

S = {
    "title": ParagraphStyle("title", fontName=BOLD, fontSize=26, leading=31, textColor=NAVY),
    "sub": ParagraphStyle("sub", fontName=FONT, fontSize=13, leading=17, textColor=TEAL),
    "h1": ParagraphStyle("h1", fontName=BOLD, fontSize=14, leading=18, textColor=NAVY, spaceBefore=4, spaceAfter=5),
    "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=10.5, leading=14, textColor=TEAL, spaceBefore=6, spaceAfter=2),
    "body": ParagraphStyle("body", fontName=FONT, fontSize=9.2, leading=12.6, spaceAfter=4),
    "small": ParagraphStyle("small", fontName=FONT, fontSize=7.9, leading=10),
    "smallb": ParagraphStyle("smallb", fontName=BOLD, fontSize=7.9, leading=10, textColor=colors.white),
    "cap": ParagraphStyle("cap", fontName=FONT, fontSize=7.8, leading=10, textColor=GREY, alignment=TA_CENTER,
                          spaceBefore=2, spaceAfter=6),
    "big": ParagraphStyle("big", fontName=BOLD, fontSize=20, leading=23, textColor=TEAL, alignment=TA_CENTER),
    "bigl": ParagraphStyle("bigl", fontName=FONT, fontSize=8, leading=10, textColor=GREY, alignment=TA_CENTER),
}


def P(text, style="body"):
    return Paragraph(text, S[style])


def bullets(items, style="body"):
    return ListFlowable([ListItem(P(t, style), leftIndent=10, value="circle") for t in items],
                        bulletType="bullet", start="\u2022", leftIndent=12, bulletFontSize=7, bulletOffsetY=-1)


def table(rows, widths, head=True, font_size=None):
    """rows: list of lists of str (header first). Cells are wrapped as Paragraphs."""
    data = []
    for i, r in enumerate(rows):
        style = "smallb" if (head and i == 0) else "small"
        data.append([P(c, style) for c in r])
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1 if head else 0)
    cmds = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if head:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), TEAL)]
        cmds += [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(2, len(rows), 2)]
    t.setStyle(TableStyle(cmds))
    return t


def shot(name, crop_h=None, width=W, caption=None):
    """Screenshot from docs/img, optionally cropped to the top crop_h pixels."""
    src = IMG / name
    im = PILImage.open(src)
    if crop_h and im.height > crop_h:
        im = im.crop((0, 0, im.width, crop_h))
        src = IMG / ("_crop_" + name)
        im.save(src)
    h = width * im.height / im.width
    out = [Image(str(src), width=width, height=h)]
    if caption:
        out.append(P(caption, "cap"))
    return out


# ---------------------------------------------------------------- diagrams
def arrow(d, x1, y1, x2, y2, color=GREY):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=1))
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    s = 5
    d.add(Polygon([x2, y2, x2 - s * math.cos(a - 0.4), y2 - s * math.sin(a - 0.4),
                   x2 - s * math.cos(a + 0.4), y2 - s * math.sin(a + 0.4)], fillColor=color, strokeColor=color))


def box(d, x, y, w, h, title, lines=(), fill=LIGHT, head=TEAL):
    d.add(Rect(x, y, w, h, fillColor=fill, strokeColor=head, strokeWidth=0.8, rx=3, ry=3))
    d.add(Rect(x, y + h - 13, w, 13, fillColor=head, strokeColor=head, rx=3, ry=3))
    d.add(String(x + w / 2, y + h - 9.5, title, fontName=BOLD, fontSize=7.6, fillColor=colors.white,
                 textAnchor="middle"))
    for i, ln in enumerate(lines):
        d.add(String(x + 4, y + h - 23 - i * 9, ln, fontName=FONT, fontSize=6.6, fillColor=NAVY))


def architecture():
    d = Drawing(W, 190)
    box(d, 0, 92, 108, 92, "Official sources", ["CMRL road diversions", "  (REST list + HTML pages)",
                                                "CMRL press releases", "  (HTML table, 2011-2026)",
                                                "RSS/Atom feeds (private ops)", "Demo agency pages"],
        fill=colors.HexColor("#fff7e6"), head=colors.HexColor("#a35b00"))
    box(d, 0, 6, 108, 70, "Scheduler", ["thread, wakes every 30 s", "runs sources when due", "backoff 10m,20m,...6h",
                                         "manual: /admin/refresh"])
    box(d, 128, 92, 112, 92, "Fetcher", ["robots.txt check (deny if", "  unreadable)", "1 req/s per host",
                                         "timeout 15-30 s", "3 attempts, 2 s / 4 s backoff", "honest User-Agent"])
    box(d, 260, 92, 112, 92, "Adapters", ["parse JSON / HTML / RSS", "skip bad items + log", "normalise type (8)",
                                          "dates -> UTC (IST source)", "routes, stations, area",
                                          "ParseError on layout change"])
    box(d, 260, 6, 112, 70, "Ingest", ["SHA-256 content hash", "idempotent upsert", "revision + change summary",
                                       "withdraw missing (only after", "  a complete fetch)"])
    box(d, 392, 92, W - 392, 92, "SQLite", ["8 tables, CHECK / UNIQUE", "UTC ISO timestamps", "notice_revisions history",
                                            "fetch_runs log", "alert_log (dedup key)"],
        fill=colors.HexColor("#eef7ee"), head=GREEN)
    box(d, 392, 6, W - 392, 70, "Flask web + API", ["faceted dropdown filters", "freshness + provenance",
                                                    "notice history, archive", "/api/notices (JSON)"],
        fill=colors.HexColor("#eef7ee"), head=GREEN)
    box(d, 128, 6, 112, 70, "Alerter", ["1 alert per (notice,", "  revision, subscriber)", "demo log or SMTP",
                                        "delivery status logged"])
    arrow(d, 128, 138, 110, 138)          # fetcher -> sources
    arrow(d, 108, 41, 180, 90)            # scheduler -> fetcher
    arrow(d, 240, 138, 258, 138)          # fetcher -> adapters
    arrow(d, 316, 92, 316, 78)            # adapters -> ingest
    arrow(d, 372, 50, 390, 120)           # ingest -> sqlite
    arrow(d, 260, 41, 242, 41)            # ingest -> alerter
    arrow(d, 440, 92, 440, 78)            # sqlite -> web
    return d


def er_diagram():
    d = Drawing(W, 236)
    ent = {
        "sources": (0, 120, 118, 112, ["source_id PK", "source_key UK", "agency, operator_type", "base_url, timezone",
                                       "refresh_seconds, enabled", "last_success_at", "consecutive_failures",
                                       "next_run_at, last_error", "label (Gov/Private)"]),
        "notices": (150, 76, 150, 156, ["notice_id PK (source:ext id)", "source_id FK", "external_id (UK w/ source)",
                                        "agency, route_id, area", "source_url, type (CHECK)", "summary, published_at",
                                        "valid_from, valid_to", "status (CHECK)", "observed_at",
                                        "last_confirmed_at", "content_hash", "revision_id, change_summary",
                                        "alert_status, alerted_at", "CHECK valid_to >= valid_from"]),
        "fetch_runs": (0, 0, 118, 104, ["run_id PK", "source_id FK", "started/finished_at", "outcome (CHECK)",
                                        "http_status", "items parsed/skipped", "new/revised/withdrawn", "error"]),
        "notice_routes": (330, 182, W - 330, 50, ["notice_id + route_id PK", "(many routes per notice)"]),
        "notice_stations": (330, 122, W - 330, 50, ["notice_id + station PK", "(many stations per notice)"]),
        "notice_revisions": (330, 40, W - 330, 72, ["notice_id + revision_id PK", "content_hash, window,",
                                                    "status, route_id, area", "change_summary"]),
        "alert_log": (150, 0, 150, 62, ["alert_id PK", "UNIQUE(notice_id,", "  revision_id, subscriber_id)",
                                        "alert_status, alerted_at"]),
        "subscribers": (330, 0, W - 330, 32, ["email UK, subscriber_filter (JSON)"]),
    }
    for name, (x, y, w, h, lines) in ent.items():
        box(d, x, y, w, h, name, lines)
    rel = [((118, 176), (150, 176), "1 : N"), ((59, 120), (59, 104), "1 : N"), ((300, 207), (330, 207), "1 : N"),
           ((300, 147), (330, 147), "1 : N"), ((300, 90), (330, 76), "1 : N"), ((225, 76), (225, 62), "1 : N"),
           ((330, 16), (300, 16), "N : 1")]
    for (x1, y1), (x2, y2), lab in rel:
        arrow(d, x1, y1, x2, y2, TEAL)
        d.add(String((x1 + x2) / 2 + 3, (y1 + y2) / 2 + 3, lab, fontName=FONT, fontSize=6, fillColor=TEAL))
    return d


# ---------------------------------------------------------------- page decoration
def on_page(c, doc):
    if doc.page == 1:
        c.setFillColor(NAVY)
        c.rect(0, A4[1] - 1.1 * cm, A4[0], 1.1 * cm, stroke=0, fill=1)
        return
    c.setStrokeColor(LINE)
    c.line(1.8 * cm, A4[1] - 1.3 * cm, A4[0] - 1.8 * cm, A4[1] - 1.3 * cm)
    c.setFont(FONT, 7.5)
    c.setFillColor(GREY)
    c.drawString(1.8 * cm, A4[1] - 1.15 * cm, "TransitNotice - Public Transport Service Update Tracker (WEB-07)")
    c.drawRightString(A4[0] - 1.8 * cm, A4[1] - 1.15 * cm, "Final report")
    c.line(1.8 * cm, 1.3 * cm, A4[0] - 1.8 * cm, 1.3 * cm)
    c.drawString(1.8 * cm, 0.9 * cm, "Cybernaut EdTech - Web Scraping SRS 2026")
    c.drawRightString(A4[0] - 1.8 * cm, 0.9 * cm, "Page %d of 8" % doc.page)


# ---------------------------------------------------------------- content
def stat_tiles(items):
    cells = [[P(v, "big") for v, _ in items], [P(l, "bigl") for _, l in items]]
    t = Table(cells, colWidths=[W / len(items)] * len(items))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                           ("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def build():
    st = []
    # ============================================================ PAGE 1 - cover + abstract
    st += [Spacer(1, 1.2 * cm), P("WEB-07 &nbsp;|&nbsp; FINAL PROJECT REPORT", "sub"), Spacer(1, 6),
           P("TransitNotice", "title"),
           P("Public Transport Service Update Tracker for Tamil Nadu", "sub"), Spacer(1, 14)]
    st.append(table([["Field", "Details"],
                     ["Team", "Team 01"],
                     ["Student name", "DIVYESH HARI G"],
                     ["Academic year", "2026 - 27"],
                     ["Programme", "Cybernaut EdTech - Web Scraping (SRS 2026), project WEB-07"],
                     ["Report date", "02 November 2026"]], [0.28, 0.72]))
    st += [Spacer(1, 16), P("Abstract", "h1"),
           P("Passengers in Chennai lose time when they do not hear about road diversions, station-entry closures, "
             "revised timings or special services. Official notices exist, but they are spread across web pages, "
             "rarely state an end date and are silently edited or removed. <b>TransitNotice</b> collects official, "
             "publicly available notices on a schedule, extracts the affected routes, stations, area, type and "
             "validity window, keeps every revision, and publishes a searchable website where each notice shows "
             "<b>when it was last verified and which official page it came from</b>. A failing source is shown as "
             "stale or unreachable - never as live."),
           P("The live system tracks two official Chennai Metro Rail Limited (CMRL) sources - Phase 2 road "
             "diversions and passenger-service press releases (2011-2026) - plus a demo government agency and a "
             "demo private operator that demonstrate the RSS path for private operators. Every filter is a "
             "drop-down list whose options are computed from the data, so every option a user can pick returns "
             "results."), Spacer(1, 8)]
    st.append(stat_tiles([("312", "notices tracked"), ("4", "sources (2 real CMRL)"), ("129", "filter options in 10 filters"),
                          ("59", "automated tests pass"), ("0 / 595", "empty results in live filter check")]))
    st += [Spacer(1, 6), P("Technology: Python 3.13, Flask, SQLite, standard-library urllib / html.parser / "
                           "xml.etree / zoneinfo. No paid APIs, no login, no CAPTCHA handling.", "small"),
           Spacer(1, 8)]
    st += shot("dashboard.png", crop_h=700, width=W * 0.78,
               caption="The TransitNotice website: freshness of every source, drop-down filters with counts.")
    st.append(PageBreak())

    # ============================================================ PAGE 2 - context
    st += [P("1. Context, stakeholders and success criteria", "h1"),
           P("<b>Problem.</b> Metro construction in Chennai (Phase 2, corridors 3, 4 and 5) has closed or diverted "
             "dozens of roads, and CMRL regularly changes entrances, parking, timings and frequencies. The "
             "information is published, but a passenger has to know where to look, cannot filter it, and cannot "
             "tell whether a notice is still valid. The project goal is a monitoring application for official "
             "public notices that makes <b>freshness</b> and <b>origin</b> visible."),
           P("User personas", "h2"),
           table([["Persona", "Needs", "How TransitNotice helps"],
                  ["<b>Priya</b>, daily commuter (Porur - Poonamallee, Corridor 4)",
                   "Know before leaving home whether her road or line is affected today",
                   "Choose <i>Corridor 4</i> and <i>Today</i> from drop-downs; one alert per new or revised notice"],
                  ["<b>Ravi</b>, MTC bus / auto driver", "Which junctions are diverted and the alternative route",
                   "Location drop-down (e.g. Kodambakkam) with the official diversion text and link to the CMRL map page"],
                  ["<b>Meera</b>, help-desk / traffic staff", "Answer 'is this still on, since when?' with a source",
                   "Start date, 'last verified' time, revision history and official notice link on every card"]],
                 [0.28, 0.32, 0.40]),
           P("Success criteria (measurable)", "h2"),
           table([["#", "Criterion", "Target", "Result (live, 02 Nov 2026)"],
                  ["S1", "Notices parsed from live sources without error", "&ge; 95 %",
                   "43 / 43 road diversions; 261 / 262 passenger press releases (99.6 %)"],
                  ["S2", "Expired notice never shown as active", "0 violations",
                   "0 - 261 ended notices labelled 'Ended' (acceptance test)"],
                  ["S3", "Duplicate alerts per revision per subscriber", "0", "0 - UNIQUE key + test"],
                  ["S4", "Source failure never turns old data into live data", "100 %",
                   "Verified by test and by a real CMRL timeout on the first run"],
                  ["S5", "Every filter option returns at least one notice", "100 %",
                   "595 live filter choices checked - 0 empty"],
                  ["S6", "Source change visible on the site", "&le; 1 refresh interval",
                   "5 min (demo sources), 6 h (CMRL - gentle on a public site)"]],
                 [0.06, 0.40, 0.14, 0.40]),
           P("Usefulness and limitations of scheduled public-source monitoring", "h2"),
           table([["Useful for", "Limitations"],
                  ["One place to see all notices with filters; history of changes that agencies do not keep; "
                   "honest freshness labels; alerts without a separate app per agency.",
                   "Not real time (up to one refresh interval old); only as good as the source (CMRL gives no end dates "
                   "for diversions); withdrawal is inferred when a notice disappears; HTML layout changes break "
                   "adapters (detected and flagged); terms of use must be re-checked; not an official channel."]],
                 [0.5, 0.5]), Spacer(1, 8)]
    st += shot("subscribe.png", crop_h=760, width=W * 0.62,
               caption="Alert sign-up for Priya's use case: only the email is typed; routes, stations, areas and "
                       "kinds are tick boxes.")
    st.append(PageBreak())

    # ============================================================ PAGE 3 - sources
    st += [P("2. Source selection, permissions and crawl policy", "h1"),
           table([["Source (key)", "URL / method", "Permission check", "Volume"],
                  ["CMRL road diversions (cmrl) - Government",
                   "chennaimetrorail.org/wp-json/wp/v2/road-diversion (public WordPress list) + each notice page",
                   "robots.txt: <i>User-agent: * / Disallow:</i> (empty = allow all); pages are index, follow",
                   "43 notices; ~45 requests on first run, then 2 per refresh (detail pages cached until modified)"],
                  ["CMRL press releases (cmrl_press) - Government",
                   "chennaimetrorail.org/press-release/ (one HTML table, all years) + station list API",
                   "Same site, same robots.txt; PDFs are linked, not downloaded",
                   "1,334 releases read, 261 passenger notices kept; 2 requests per refresh"],
                  ["Sample Chennai Transit (sample_metro) - demo", "HTML page served by the app (/demo-source/)",
                   "Own robots.txt allows /demo-source/", "4 notices; mentors can change it live"],
                  ["Sample Private Coaches (demo_private_bus) - Private", "RSS feed served by the app",
                   "Fictional operator, labelled 'demo' everywhere", "4 notices; proves the private-operator path"],
                  ["MBTA (mbta) - optional", "api-v3.mbta.com/alerts (open developer API)", "robots 404; anonymous use allowed",
                   "Disabled by default"]],
                 [0.20, 0.28, 0.27, 0.25]),
           P("Other Tamil Nadu sources checked and not used", "h2"),
           table([["Source", "Finding", "Decision"],
                  ["Southern Railway (sr.indianrailways.gov.in)", "robots.txt returns an error page - policy unreadable",
                   "Not crawled (unreadable robots = deny)"],
                  ["MTC Chennai, Chennai Port, AAI", "TLS certificate chain cannot be verified", "Not crawled (no TLS bypass)"],
                  ["PIB regional RSS, IntrCity", "HTTP 403 to bots", "Not crawled"],
                  ["TNSTC, Parveen, Namma Yatri, KPN, YoloBus", "Booking apps; no notice page or feed", "Nothing to collect"],
                  ["Freshbus, Zingbus", "robots.txt blocks several crawlers; no feed", "Not crawled"],
                  ["Chennai Traffic Police, SETC, SRS Travels", "Domain does not resolve", "Not reachable"]],
                 [0.34, 0.40, 0.26]),
           Spacer(1, 5),
           P("No private Tamil Nadu operator currently publishes notices in a collectable form. The system is ready "
             "for them: an operator with an RSS/Atom feed is added in <b>agencies.json</b> with "
             "<i>operator_type: Private</i>, without code changes. Notices of real companies are never invented.",
             "body"),
           P("Crawl policy", "h2"),
           bullets(["Honest User-Agent: <i>TransitNoticeBot/1.0 (student project; contact: ...)</i>.",
                    "robots.txt checked before each new site: 404 = allowed; error, 5xx, 401, 403 or unreadable = denied.",
                    "Rate limit 1 request/second per host; timeout 15 s (30 s for the slow CMRL site); at most 3 "
                    "attempts per request with 2 s and 4 s backoff; retries only for network errors, 429 and 5xx.",
                    "After a failed refresh the next run is pushed back 10 min, 20 min, 40 min ... up to 6 h.",
                    "No login, no CAPTCHA bypass, no private data. The only personal data is a subscriber's email and "
                    "filter, given with consent; secrets live in .env, never in source code."]), Spacer(1, 6)]
    st += shot("status.png", crop_h=560, width=W * 0.82,
               caption="Data sources page: operator type, state, last attempt, last success, failures in a row and "
                       "next scheduled run for every source (the fetch-run log follows below it).")
    st.append(PageBreak())

    # ============================================================ PAGE 4 - SRS
    st += [P("3. Software requirements specification", "h1"),
           P("Use cases", "h2"),
           table([["ID", "Actor", "Use case"],
                  ["UC1", "Passenger", "Browse notices by choosing operator type, agency, route, station, area, kind, "
                                       "status, travel day, year and end date from drop-downs; each option shows a count"],
                  ["UC2", "Passenger", "Open a notice: window, official link, last verification, revision history"],
                  ["UC3", "Passenger", "Subscribe (email + tick boxes); receive one alert per new or revised match"],
                  ["UC4", "Passenger", "Browse ended, withdrawn and cancelled notices (archive)"],
                  ["UC5", "Scheduler", "Refresh each source when due; back off after failures"],
                  ["UC6", "Admin", "Manual refresh, source health, fetch-run log, alert delivery log (token protected)"],
                  ["UC7", "Integrator", "Query /api/notices (JSON) with the same filters"],
                  ["UC8", "Admin", "Add a government or private operator via agencies.json (no code)"]],
                 [0.07, 0.13, 0.80]),
           P("Requirements traceability", "h2"),
           table([["Requirement (brief sections 5 and 7)", "Implementation"],
                  ["Collect public alerts from permitted sources", "sources/cmrl.py, cmrl_press.py, feed.py; robots check in base.py"],
                  ["Extract route, type, issue and valid window", "Adapters produce ParsedNotice; normalize.py (8 types); timeutil.py"],
                  ["Filter by route, station, area and service date", "queries.search + notice_routes / notice_stations; faceted drop-downs"],
                  ["Active status from announced window", "ingest.effective_status and queries.STATUS_SQL (computed at request time)"],
                  ["Track withdrawn, revised, extended", "content hash, notice_revisions, describe_change, withdraw_missing"],
                  ["Last checked + provenance", "last_confirmed_at, source_url, fetch_runs, Data sources page"],
                  ["Single alert per new / revised advisory", "alert_log UNIQUE(notice_id, revision_id, subscriber_id)"],
                  ["Degrade gracefully", "failed run never touches notices; per-item skip; ParseError guard"],
                  ["Backoff, timeout, retry ceiling, failure logs", "Fetcher.get, refresh_source (next_run_at), fetch_runs"],
                  ["Constraints, idempotent upserts, access control, secrets", "CHECK / UNIQUE; hash compare; admin token from env"],
                  ["Responsive, accessible UI and charts", "CSS grid + mobile breakpoint; SVG charts with titles and data tables"]],
                 [0.45, 0.55]),
           P("Architecture", "h2"), architecture(),
           P("Figure 1 - Data flow from official sources to the website and alerts.", "cap"),
           PageBreak()]

    # ============================================================ PAGE 5 - database
    st += [P("3.1 Database design (ER diagram)", "h1"), er_diagram(),
           P("Figure 2 - SQLite schema (migrations/001_init.sql, 002_operator_type.sql).", "cap"),
           P("Keys and repeat-run behaviour", "h2"),
           bullets(["<b>notice_id = source_key:external_id</b> is stable (CMRL post id, press-release PDF name, RSS guid); "
                    "UNIQUE(source_id, external_id) prevents duplicates.",
                    "<b>Idempotent upsert:</b> a re-run with identical content only updates <i>last_confirmed_at</i>. "
                    "Verified: three consecutive refreshes produce 0 new and 0 revised rows.",
                    "<b>Revisions:</b> a different SHA-256 content hash (type, text, window, routes, stations, area, source "
                    "status) increments revision_id, snapshots the row into notice_revisions and stores a readable "
                    "change summary, e.g. <i>Extended: end 02 Nov 2026 23:03 IST -&gt; 03 Nov 2026 23:03 IST</i>.",
                    "<b>Withdrawal:</b> only after a complete, successful fetch are notices that the source no longer "
                    "lists marked <i>withdrawn</i> (kept in the archive); items that could not be parsed are protected.",
                    "<b>Constraints:</b> CHECK on type, status and outcome values; CHECK valid_to &ge; valid_from; "
                    "foreign keys with cascade; schema_migrations table makes migrations run exactly once."]),
           P("Time-zone policy", "h2"),
           table([["Rule", "Example"],
                  ["Store UTC only (YYYY-MM-DDTHH:MM:SSZ) so text order = time order", "2026-11-05T16:30:00Z"],
                  ["Naive source times use the source zone (Asia/Kolkata)", "'2026-11-05 22:00' IST -&gt; 16:30 UTC"],
                  ["Date-only end dates run to 23:59:59 local", "'5 Nov 2026' -&gt; 2026-11-05T18:29:59Z"],
                  ["'TBD', empty, 'until further notice'", "valid_to = NULL (open ended)"],
                  ["Travel-day filter uses IST calendar days", "'3 Nov' = 2 Nov 18:30 UTC to 3 Nov 18:29:59 UTC"]],
                 [0.58, 0.42]),
           PageBreak()]

    # ============================================================ PAGE 6 - implementation
    st += [P("4. Implementation", "h1"),
           P("<b>Collection.</b> A Fetcher shared by all adapters checks robots.txt once per site, throttles to one "
             "request per second, and retries with exponential backoff. CMRL diversions are listed through the "
             "site's public WordPress REST endpoint; each detail page is parsed with an html.parser state machine "
             "(title, description paragraphs or list items, 'Diversion Start Date' and 'CMRL Package' cards, "
             "'Last Updated On'). Detail pages are cached and re-downloaded only when the list reports a new "
             "modified time. Press releases come from one HTML table; titles are classified with keyword rules "
             "(passenger notices kept, corporate news skipped), dates are read from titles ('between 20.10.2025 and "
             "24.10.2025', 'January 14, 15 and 16'), and stations are matched against CMRL's station list, which "
             "also gives each station's line (Blue / Green)."),
           P("<b>Robust parsing.</b> Unclosed tags close implicitly when the next field starts; items without id, text "
             "or route/area are skipped with a diagnostic; duplicates and backwards windows are rejected. A page "
             "with no notices and no explicit 'empty' marker raises ParseError, so a layout change can never "
             "mass-withdraw data. One slow CMRL page (a real 25 s timeout on the first live run) now only skips that "
             "notice - its stored data stays, unverified - instead of failing the whole refresh."),
           P("<b>Scheduling and alerts.</b> A background thread wakes every 30 s and runs each source whose "
             "next_run_at is due. Changes are passed to the Alerter, which writes one alert_log row per "
             "(notice, revision, subscriber) with INSERT OR IGNORE, so repeated refreshes never resend. Demo mode "
             "writes to logs/alerts.log; SMTP is configured through environment variables."),
           P("<b>Website.</b> Every filter is a drop-down. Its options are computed from the notices that match all "
             "the <i>other</i> chosen filters, shown with counts, and zero-count options are hidden - so every "
             "choice returns results. Chosen filters appear as removable chips; notices in effect now are listed "
             "first; each card shows status in plain words, type, operator type, window, agency, 'checked N min ago' "
             "and the official link.")]
    st += shot("filtered.png", crop_h=1040, width=W * 0.86,
               caption="Figure 3 - Dashboard filtered to Blue Line + station / entry / parking closures: counts in "
                       "every drop-down, removable chips, current notice first, ended notices clearly labelled.")
    st.append(PageBreak())

    # ============================================================ PAGE 7 - testing & evaluation
    st += [P("5. Testing", "h1"),
           P("59 automated tests (python -m unittest discover -s tests) - all pass. Fixtures cover malformed HTML, "
             "missing fields, duplicates, layout changes, unavailable sources, RSS/Atom feeds and CMRL page layouts."),
           table([["Test (mandatory acceptance tests first)", "Input", "Expected", "Result"],
                  ["Expired advisory not marked active", "Window ended 1 Oct, now 2 Nov", "Status 'Ended', only in archive", "PASS"],
                  ["Route filter with multiple routes", "Notice on Red + Orange", "Found by 'Red' and by 'orange'", "PASS"],
                  ["Extended advisory updates history", "valid_to +1 day", "Same notice, rev 2, 'Extended ...'", "PASS"],
                  ["Time-zone handling reproducible", "IST / EST / EDT / offsets / date-only", "UTC values in policy table", "PASS"],
                  ["Stale notice shows latest verification", "Last success 5 h ago", "'Stale - last verified 5 h ago'", "PASS"],
                  ["Source error does not make old data live", "HTTP 503 after a good run", "last_confirmed unchanged; 'error'", "PASS"],
                  ["Idempotent refresh / withdraw + reinstate", "Same data twice; item removed then back", "0 changes; withdrawn -&gt; active rev 3", "PASS"],
                  ["Malformed HTML, missing fields, duplicates", "4 HTML fixtures", "Good items kept, bad skipped + logged", "PASS"],
                  ["Layout change / empty list", "Error page; '[]'", "ParseError, nothing withdrawn", "PASS"],
                  ["Retry ceiling, backoff, robots.txt", "503 forever; Disallow; robots 500", "3 attempts (2 s, 4 s); denied", "PASS"],
                  ["One alert per revision", "3 refreshes + 1 revision, 2 subscribers", "2 alerts, then 4", "PASS"],
                  ["CMRL detail page down", "1 of 3 pages returns 503", "Run OK; that notice kept, not re-verified", "PASS"],
                  ["Press-release classification and windows", "12-row fixture", "8 kept; exact IST windows; lines", "PASS"],
                  ["Every drop-down option has results", "All options, several filter combinations", "Count shown = rows returned &gt; 0", "PASS"],
                  ["Security", "No admin token; &lt;script&gt; in text", "403; output escaped", "PASS"]],
                 [0.33, 0.24, 0.33, 0.10]),
           P("Live checks on the running system (02 Nov 2026)", "h2"),
           bullets(["CMRL diversions: 43 parsed, 0 skipped. CMRL press releases: 1,334 rows read, 261 passenger notices "
                    "kept, 1 skipped (date written only as '2016').",
                    "Mentor challenges on the demo source: <i>extend SM-101 24</i> -&gt; revision with 'Extended'; "
                    "<i>open-ended</i> -&gt; 'No end date announced'; <i>break</i> -&gt; SOURCE ERROR, data kept, retry "
                    "later; <i>withdraw</i> -&gt; moved to archive.",
                    "Filter crawl: all 129 options plus sampled pairs = 595 requests, <b>0 empty results</b>; dashboard "
                    "with 312 notices renders in about 0.1 s."]),
           P("6. Evaluation", "h1"),
           table([["Area", "Result"],
                  ["Collection quality", "100 % of diversions and 99.6 % of passenger press releases parsed. A keyword "
                   "bug ('shuttle' matched 'shut') was found from screenshots and fixed with a test."],
                  ["Alert precision", "Alerts only when route, station, area and type filters all match; none for notices "
                   "already ended when first seen; never repeated for the same revision."],
                  ["Refresh performance", "CMRL diversions: ~1 min on first run, then 2-3 requests; press releases: 2 "
                   "requests (900 KB page); demo sources every 5 min."],
                  ["Limitations", "Press releases carry no validity: undated ones are current for 14 days; durations "
                   "such as 'for two months' are not interpreted; PDF details are linked, not parsed; CMRL diversions "
                   "have no end dates; SQLite suits a single instance."]],
                 [0.2, 0.8]),
           PageBreak()]

    # ============================================================ PAGE 8 - deployment, ethics, appendix
    st += [P("7. Deployment and operations", "h1"),
           bullets(["Install: <i>python -m venv .venv</i>, <i>pip install -r requirements.txt</i> (Flask, tzdata).",
                    "Configure: copy .env.example to .env and set TN_ADMIN_TOKEN (long, random), TN_CONTACT, "
                    "TN_ALERT_MODE (demo / smtp + SMTP settings). .env is git-ignored; agencies.json lists sources.",
                    "Run: <i>python run.py serve</i> (web + scheduler); <i>python run.py refresh</i> for a one-off refresh; "
                    "migrations apply automatically and only once.",
                    "Monitor: GET /healthz for uptime; the Data sources page shows failures in a row, last error and next "
                    "run; fetch_runs keeps every run with counts and diagnostics."]),
           P("8. Ethics, compliance and conclusion", "h1"),
           P("TransitNotice reads only public pages that robots.txt allows, identifies itself, rate-limits, prefers "
             "structured endpoints, and never logs in or bypasses protections. Sources whose policy could not be read "
             "were left out rather than guessed. Notices contain no personal data; subscribers give only an email and "
             "a filter. Every card shows freshness and links to the official notice, and the footer states that the "
             "site is unofficial. <b>Known gaps:</b> no unsubscribe link in alert emails yet, English only, no real "
             "private operator feed available. <b>Future work:</b> Tamil language interface, PDF text extraction for "
             "press releases, more agencies once their policies allow it, PostgreSQL, web-push alerts and a map view."),
           P("In conclusion, the project meets the brief: official Tamil Nadu notices are collected politely, "
             "normalised, versioned and archived; freshness and provenance are always visible; alerts are "
             "de-duplicated; failures degrade gracefully; and the website is usable without typing."),
           P("Appendix", "h1"),
           P("A. Sample stored records (from /api/notices)", "h2"),
           table([["Field", "Road diversion", "Press release"],
                  ["notice_id", "cmrl:10287", "cmrl_press:Press-Release-21.10.2026-English.pdf"],
                  ["summary", "Otteri Traffic Diversion: Traffic flow towards Ayanavaram ... is not changed.",
                   "Phase 2 Corridor-4 Stage 1A &amp; 1B Passenger Service: Revised Service Timings"],
                  ["route_id / area", "Corridor 3 / Chennai - Corridor 3 (Madhavaram - SIPCOT)", "Corridor 4 / Chennai - Corridor 4"],
                  ["type / status", "detour / active (no end date announced)", "schedule_change / active"],
                  ["valid_from -&gt; valid_to (UTC)", "2025-03-29T18:30:00Z -&gt; NULL", "2026-10-20T18:30:00Z -&gt; 2026-11-04T18:29:59Z"],
                  ["source_url", "chennaimetrorail.org/road-diversion/otteri/", "chennaimetrorail.org/.../Press-Release-21.10.2026-English.pdf"]],
                 [0.2, 0.4, 0.4]),
           P("B. Notice detail page (official text and provenance; on the site the revision history is listed below)",
             "h2")]
    st += shot("notice.png", crop_h=620, width=W * 0.66)
    st += [P("All screenshots are in docs/img/: dashboard.png, filtered.png, notice.png, status.png, subscribe.png.",
             "small"),
           Spacer(1, 3),
           P("C. Git commit evidence: ________________________________________________ "
             "(paste <i>git log --oneline</i> output here).", "small")]

    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.7 * cm,
                            bottomMargin=1.6 * cm, title="TransitNotice - Final Report (WEB-07)",
                            author="DIVYESH HARI G (Team 01)", subject="Public Transport Service Update Tracker")
    doc.build(st, onFirstPage=on_page, onLaterPages=on_page)
    for f in IMG.glob("_crop_*"):
        f.unlink()
    print("wrote", OUT)


if __name__ == "__main__":
    build()
