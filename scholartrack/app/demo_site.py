"""A fictional, local 'announcement site' with two different layouts.

It exists so the whole pipeline can be demonstrated and tested safely (and so a mentor can edit a
notice live). All providers are invented. Real sources are added in sources.json after approval.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from . import config

router = APIRouter(prefix="/demo-source", tags=["demo-source"])
_lock = threading.Lock()
STATE_FILE = config.DB_PATH.parent / "demo_state.json"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _fmt(d: datetime, style: str) -> str:
    mon, day, year = MONTHS[d.month - 1], d.day, d.year
    return {
        "dmy_ist": f"{day} {mon} {year}, 17:00 IST",
        "date_only": f"{day} {mon} {year}",
        "iso_offset": f"{d:%Y-%m-%d}T23:59:00+05:30",
        "mdy_est": f"{mon} {day}, {year} 11:59 PM EST",
        "dmy_utc": f"{day} {mon} {year}, 23:59 UTC",
        "utc_plus": f"{day} {mon} {year}, 18:00 UTC+01:00",
        "numeric_cet": f"{day:02d}/{d.month:02d}/{year} 12:00 CET",
        "tba": "To be announced",
        "rolling": "Rolling",
    }[style]


def _seed() -> dict:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    def n(portal, key, title, provider, level, field, category, benefit, elig, days, style, status="Open", region="India"):
        return {"portal": portal, "key": key, "title": title, "provider": provider, "level": level,
                "field": field, "category": category, "benefit": benefit, "eligibility": elig,
                "deadline": _fmt(today + timedelta(days=days), style), "status": status, "region": region}
    items = [
        n("a", "aurora-merit", "Aurora Merit Scholarship", "Aurora Education Trust (demo)", "Undergraduate",
          "Engineering", "Merit", "Tuition waiver up to INR 150,000 per year",
          "Indian citizens enrolled full-time in year 2 or above with at least 8.5 CGPA.", 9, "dmy_ist"),
        n("a", "riverbend-bursary", "Riverbend Need-Based Bursary", "Riverbend Foundation (demo)", "Undergraduate",
          "Any programme", "Need-based", "INR 60,000 one-time grant",
          "Family income below INR 400,000 per year. Residents of the state only.", 24, "date_only"),
        n("a", "lakeshore-research", "Lakeshore Postgraduate Research Grant", "Lakeshore Institute (demo)", "Postgraduate",
          "Science", "Research grant", "Up to INR 200,000 for thesis research",
          "Master's students with a faculty-approved research proposal. Age below 30.", 40, "iso_offset"),
        n("a", "meridian-stem-women", "Meridian STEM Scholarship for Women", "Meridian Corp (demo)", "Undergraduate",
          "STEM", "Merit", "INR 100,000 per year",
          "Women enrolled in a STEM bachelor's programme; minimum 75% marks.", 0, "tba"),
        n("a", "summit-withdrawn", "Summit Excellence Scholarship", "Summit Group (demo)", "Undergraduate",
          "Commerce", "Merit", "INR 80,000", "Top-ranked students in the final year.", 15, "dmy_ist", status="Withdrawn"),
        n("a", "harbor-doctoral", "Harbor Doctoral Fellowship", "Harbor University (demo)", "Doctoral",
          "Humanities", "Fellowship", "Stipend of USD 24,000 per year",
          "PhD candidates; open to international applicants. IELTS 7.0 required.", 60, "mdy_est", region="Global"),
        n("b", "global-horizons", "Global Horizons Fellowship", "Horizons Council (demo)", "Postgraduate",
          "Public policy", "Fellowship", "Fully funded 1-year programme",
          "Citizens of eligible countries with 2 years of work experience. Age below 35.", 18, "dmy_utc", region="Global"),
        n("b", "evergreen-first-gen", "Evergreen First-Generation Scholarship", "Evergreen Fund (demo)", "Undergraduate",
          "Any programme", "Need-based", "GBP 3,000 per year",
          "First-generation university students; household income evidence required.", 30, "utc_plus", region="UK"),
        n("b", "tidewater-travel", "Tidewater Travel Grant for Researchers", "Tidewater Society (demo)", "Doctoral, Postdoctoral",
          "Marine science", "Travel grant", "Up to EUR 1,500 conference travel",
          "Early-career researchers presenting at an accredited conference.", 6, "numeric_cet", region="Europe"),
        n("b", "ironwood-school", "Ironwood Merit Award for School Students", "Ironwood Academy (demo)", "School",
          "Mathematics", "Merit", "INR 25,000 award", "Class XI and XII students; top 5% in the board exam.", 45, "date_only"),
        n("b", "cobalt-open", "Cobalt Open Fellowship", "Cobalt Labs (demo)", "Postgraduate",
          "Computer science", "Fellowship", "Mentorship plus stipend", "Open to enrolled master's students.", 0, "rolling"),
    ]
    return {"mode": {"a": "ok", "b": "ok"}, "items": items, "seed_day": today.strftime("%Y-%m-%d")}


def _load() -> dict:
    with _lock:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return reset()


def _save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def reset() -> dict:
    state = _seed()
    with _lock:
        _save(state)
    return state


def apply_action(payload: dict) -> dict:
    """Mentor/operator controls used by the walkthrough panel and the tests."""
    state = _load()
    action = payload.get("action")
    items = {i["key"]: i for i in state["items"]}
    if action == "reset":
        state = reset()
    elif action == "set_deadline" and payload.get("key") in items:
        items[payload["key"]]["deadline"] = str(payload.get("text", ""))[:80]
    elif action == "withdraw" and payload.get("key") in items:
        items[payload["key"]]["status"] = "Withdrawn"
    elif action == "restore" and payload.get("key") in items:
        items[payload["key"]]["status"] = "Open"
    elif action == "set_mode" and payload.get("portal") in ("a", "b") and payload.get("mode") in ("ok", "down", "layout"):
        state["mode"][payload["portal"]] = payload["mode"]
    elif action == "add" and payload.get("portal") in ("a", "b"):
        key = f"mentor-{len(state['items']) + 1}"
        state["items"].append({
            "portal": payload["portal"], "key": key, "title": str(payload.get("title", "Mentor Test Scholarship"))[:120],
            "provider": "Mentor Demo Trust (demo)", "level": "Undergraduate", "field": "Any programme",
            "category": "Merit", "benefit": "Test award", "eligibility": "Created live by the mentor.",
            "deadline": str(payload.get("text", "To be announced"))[:80], "status": "Open", "region": "India"})
    else:
        raise ValueError("unknown or invalid demo action")
    if action != "reset":
        with _lock:
            _save(state)
    return state


# ---------- rendering

PAGE = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{title}</title></head>"
        "<body><h1>{title}</h1><p>Fictional demo announcements for ScholarTrack.</p>{body}</body></html>")


def _gate(portal: str):
    mode = _load()["mode"][portal]
    if mode == "down":
        return Response("Service temporarily unavailable", status_code=503)
    if mode == "layout":
        return HTMLResponse(PAGE.format(title="Redesigned portal",
                                        body="<div class='grid'><div class='tile'>We moved! Try our new search.</div></div>"))
    return None


@router.get("/robots.txt")
def robots() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /demo-source/private/\nCrawl-delay: 0\n")


@router.get("/portal-a", response_class=HTMLResponse)
def portal_a():
    blocked = _gate("a")
    if blocked:
        return blocked
    rows = []
    for i in (x for x in _load()["items"] if x["portal"] == "a"):
        cls = " class='withdrawn'" if i["status"] == "Withdrawn" else ""
        rows.append(
            f"<tr data-id='{escape(i['key'])}'{cls}><td><a href='/demo-source/notice/{escape(i['key'])}'>{escape(i['title'])}</a></td>"
            f"<td>{escape(i['provider'])}</td><td>{escape(i['level'])}</td><td>{escape(i['field'])}</td>"
            f"<td>{escape(i['category'])}</td><td>{escape(i['benefit'])}</td><td>{escape(i['eligibility'])}</td>"
            f"<td>{escape(i['deadline'])}</td><td>{escape(i['status'])}</td></tr>")
    table = ("<table class='notices'><thead><tr><th>Scholarship</th><th>Provider</th><th>Level</th><th>Programme</th>"
             "<th>Category</th><th>Benefit</th><th>Eligibility</th><th>Deadline</th><th>Status</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table>")
    return PAGE.format(title="Demo National Scholarship Portal", body=table)


@router.get("/portal-b", response_class=HTMLResponse)
def portal_b():
    blocked = _gate("b")
    if blocked:
        return blocked
    cards = []
    for i in (x for x in _load()["items"] if x["portal"] == "b"):
        badge = "<span class='badge'>Withdrawn</span>" if i["status"] == "Withdrawn" else ""
        cards.append(
            f"<article class='notice' data-id='{escape(i['key'])}'><h3><a href='/demo-source/notice/{escape(i['key'])}'>"
            f"{escape(i['title'])}</a></h3>{badge}<dl>"
            f"<dt>Funder</dt><dd>{escape(i['provider'])}</dd><dt>Study level</dt><dd>{escape(i['level'])}</dd>"
            f"<dt>Field</dt><dd>{escape(i['field'])}</dd><dt>Region</dt><dd>{escape(i['region'])}</dd>"
            f"<dt>Type</dt><dd>{escape(i['category'])}</dd><dt>Value</dt><dd>{escape(i['benefit'])}</dd>"
            f"<dt>Who can apply</dt><dd>{escape(i['eligibility'])}</dd><dt>Closes</dt><dd>{escape(i['deadline'])}</dd>"
            f"</dl></article>")
    return PAGE.format(title="Demo International Fellowship Board", body="".join(cards))


@router.get("/notice/{key}", response_class=HTMLResponse)
def official_notice(key: str):
    for i in _load()["items"]:
        if i["key"] == key:
            return PAGE.format(title=escape(i["title"]), body=(
                f"<p><strong>Provider:</strong> {escape(i['provider'])}</p>"
                f"<p><strong>Benefit:</strong> {escape(i['benefit'])}</p>"
                f"<p><strong>Eligibility:</strong> {escape(i['eligibility'])}</p>"
                f"<p><strong>Deadline:</strong> {escape(i['deadline'])}</p>"
                f"<p><strong>Status:</strong> {escape(i['status'])}</p>"))
    return HTMLResponse("Not found", status_code=404)
