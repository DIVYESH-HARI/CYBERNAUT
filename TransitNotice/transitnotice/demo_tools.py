"""Controls the fictional 'Sample Metro' source page (demo_source/notices.html) for demos and mentor challenges.

  python run.py demo reset                 fresh, time-relative sample data
  python run.py demo extend SM-101 24      extend an outage by 24 hours
  python run.py demo open-ended            add an advisory without an end date
  python run.py demo withdraw SM-102       remove an advisory from the page
  python run.py demo cancel SM-103         mark an advisory cancelled by the source
  python run.py demo break                 serve a layout-changed page (simulated parser failure)
  python run.py demo corrupt               serve malformed HTML with missing fields and duplicates
"""
import html
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT

DIR = ROOT / "demo_source"
STATE = DIR / "state.json"
PAGE = DIR / "notices.html"
TZ = ZoneInfo("Asia/Kolkata")


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _now():
    return datetime.now(TZ).replace(second=0, microsecond=0)


def fresh_state():
    n = _now()
    return {"notices": [
        {"id": "SM-101", "type": "Suspension", "routes": ["Demo Line A", "Demo Line B"], "stations": ["Guindy", "Saidapet"],
         "area": "South Chennai (demo)", "title": "Track maintenance outage", "published": _fmt(n - timedelta(hours=3)),
         "from": _fmt(n - timedelta(hours=2)), "to": _fmt(n + timedelta(hours=10)),
         "summary": "Demo: trains suspended between Guindy and Saidapet. Free shuttle buses operate."},
        {"id": "SM-102", "type": "Detour", "routes": ["Demo Bus 21"], "stations": ["T. Nagar"], "area": "Central Chennai (demo)",
         "title": "T. Nagar bus diversion", "published": _fmt(n - timedelta(days=1)), "from": _fmt(n - timedelta(hours=20)),
         "to": _fmt(n + timedelta(days=2)), "summary": "Demo: Bus 21 diverted via Usman Road; T. Nagar stop not served."},
        {"id": "SM-103", "type": "Station closure", "routes": ["Demo Line C"], "stations": ["Tambaram"],
         "area": "South Chennai (demo)", "title": "Tambaram stop closed", "published": _fmt(n - timedelta(hours=6)),
         "from": _fmt(n + timedelta(hours=12)), "to": _fmt(n + timedelta(days=3)),
         "summary": "Demo: Tambaram stop closed for platform works. Use the next stop."},
        {"id": "SM-104", "type": "Delay", "routes": ["Demo Line B"], "stations": [], "area": "Central Chennai (demo)",
         "title": "Signal fault delays", "published": _fmt(n - timedelta(days=2)), "from": _fmt(n - timedelta(days=2)),
         "to": _fmt(n - timedelta(hours=1)), "summary": "Demo: signal fault caused delays of up to 15 minutes (cleared)."},
    ], "mode": "normal"}


def load():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else fresh_state()


def render(state):
    if state.get("mode") == "break":
        return "<html><body><h1>Site maintenance</h1><p>Our website is being redesigned.</p></body></html>"
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Sample Metro - Service notices</title></head><body>',
             '<section id="notices" %s>' % ('data-empty="true"' if not state["notices"] else "")]
    items = list(state["notices"])
    if state.get("mode") == "corrupt":
        items = items + [dict(items[0])] if items else items                       # duplicate id
        items.append({"id": "SM-900", "type": "Delay", "routes": [], "stations": [], "area": "", "title": "",
                      "summary": "", "published": "", "from": "", "to": ""})        # missing fields
        items.append({"id": "SM-901", "type": "Other", "routes": ["Line 9"], "stations": [], "area": "West",
                      "title": "Bad dates", "summary": "valid_to is before valid_from", "published": "yesterday-ish",
                      "from": "2026-12-05 10:00", "to": "2026-12-01 10:00"})
    for n in items:
        parts.append('<article class="notice" data-id="%s" data-status="%s">' % (html.escape(n["id"]), n.get("status", "active")))
        parts.append('<h2 class="title">%s</h2>' % html.escape(n["title"]))
        parts.append('<span class="type">%s</span>' % html.escape(n["type"]))
        parts.append('<ul class="routes">%s</ul>' % "".join("<li>%s</li>" % html.escape(r) for r in n["routes"]))
        parts.append('<span class="stations">%s</span>' % html.escape("; ".join(n["stations"])))
        parts.append('<span class="area">%s</span>' % html.escape(n["area"]))
        parts.append('<time class="published">%s</time> <time class="from">%s</time> <time class="to">%s</time>'
                     % (n["published"], n["from"], n["to"] or "TBD"))
        parts.append('<p class="summary">%s</p>' % html.escape(n["summary"]))
        if state.get("mode") == "corrupt":
            parts.append("<div><span>unclosed")                                     # malformed on purpose
        parts.append('<a class="more" href="notices.html#%s">Details</a></article>' % html.escape(n["id"]))
    parts.append("</section></body></html>")
    return "\n".join(parts)


PRIVATE_FEED = DIR / "private_coaches.xml"


def render_private_feed():
    """RSS feed of the fictional private operator 'Sample Private Coaches' (dates relative to now)."""
    n = _now()
    d = lambda dt: dt.strftime("%d %b %Y %H:%M")
    rfc = lambda dt: dt.strftime("%a, %d %b %Y %H:%M:%S +0530")
    items = [
        ("PC-1", "Boarding point change for night services",
         "Demo: all Chennai night departures board at Kilambakkam bus terminus from %s until further notice."
         % d(n - timedelta(days=1)), ["Chennai - Madurai", "Chennai - Tirunelveli"], n - timedelta(days=1, hours=2)),
        ("PC-2", "Trip not running: Chennai - Coimbatore 22:30 departure",
         "Demo: the 22:30 departure is not running on %s. Passengers are moved to the 23:15 coach."
         % (n + timedelta(days=1)).strftime("%d %b %Y"), ["Chennai - Coimbatore"], n - timedelta(hours=5)),
        ("PC-3", "Delays expected due to heavy rain",
         "Demo: departures may run 30-45 minutes late from %s to %s."
         % (d(n - timedelta(hours=1)), d(n + timedelta(hours=20))), ["Chennai - Trichy", "Chennai - Madurai"],
         n - timedelta(hours=1)),
        ("PC-4", "Festival extra services",
         "Demo: extra coaches ran from %s to %s (schedule change)."
         % (d(n - timedelta(days=9)), d(n - timedelta(days=6))), ["Chennai - Salem"], n - timedelta(days=10)),
    ]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>',
             "<title>Sample Private Coaches - service notices (demo)</title>",
             "<link>http://127.0.0.1/demo-source/private_coaches.xml</link>",
             "<description>Fictional private operator used to demonstrate the RSS adapter</description>"]
    for gid, title, text, cats, pub in items:
        parts.append("<item><guid>%s</guid><title>%s</title><link>%s#%s</link><pubDate>%s</pubDate>%s"
                     "<description>%s</description></item>"
                     % (gid, html.escape(title), "private_coaches.xml", gid, rfc(pub),
                        "".join("<category>%s</category>" % html.escape(c) for c in cats), html.escape(text)))
    parts.append("</channel></rss>")
    return "\n".join(parts)


def save(state):
    DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    PAGE.write_text(render(state), encoding="utf-8")
    if not PRIVATE_FEED.exists() or state.get("_reset"):
        PRIVATE_FEED.write_text(render_private_feed(), encoding="utf-8")


def _find(state, nid):
    for n in state["notices"]:
        if n["id"] == nid:
            return n
    raise SystemExit("no such notice %s" % nid)


def command(args):
    cmd = args[0] if args else "reset"
    st = load()
    if cmd == "reset":
        st = fresh_state()
        st["_reset"] = True
    elif cmd == "extend":
        n = _find(st, args[1])
        hours = float(args[2]) if len(args) > 2 else 24
        n["to"] = _fmt(datetime.strptime(n["to"], "%Y-%m-%d %H:%M") + timedelta(hours=hours))
    elif cmd == "open-ended":
        st["notices"].append({"id": "SM-200", "type": "Delay", "routes": ["Demo Line A"], "stations": [],
                              "area": "South Chennai (demo)", "title": "Ongoing power issue", "published": _fmt(_now()),
                              "from": _fmt(_now()), "to": "", "summary": "Demo: intermittent delays; no end time announced yet."})
    elif cmd == "withdraw":
        st["notices"].remove(_find(st, args[1]))
    elif cmd == "cancel":
        _find(st, args[1])["status"] = "cancelled"
    elif cmd in ("break", "corrupt", "normal"):
        st["mode"] = cmd
    else:
        raise SystemExit(__doc__)
    save(st)
    st.pop("_reset", None)
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    print("demo source updated:", cmd, " ".join(args[1:]))
