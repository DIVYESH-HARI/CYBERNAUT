"""Turn messy published text into clean, comparable fields.

Core rule: a deadline we cannot read is UNKNOWN (None). We never invent a date.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UNKNOWN_RE = re.compile(
    r"^\s*(?:|-|–|—|n/?a|tba|tbd|tbc|to be (?:announced|confirmed|decided|notified)"
    r"|not (?:yet )?(?:announced|available|published|specified)|rolling|varies|ongoing"
    r"|open until filled)\s*\.?\s*$",
    re.I,
)

# Abbreviations are ambiguous in general; these are the common readings, documented in the README.
TZ_ABBR = {
    "UTC": "UTC", "GMT": "UTC", "Z": "UTC", "IST": "Asia/Kolkata", "BST": "Europe/London",
    "CET": "Europe/Paris", "CEST": "Europe/Paris", "EET": "Europe/Athens",
    "EST": "America/New_York", "EDT": "America/New_York", "CST": "America/Chicago",
    "CDT": "America/Chicago", "MST": "America/Denver", "MDT": "America/Denver",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles", "JST": "Asia/Tokyo",
    "SGT": "Asia/Singapore", "AEST": "Australia/Sydney", "AEDT": "Australia/Sydney",
    "NZST": "Pacific/Auckland", "PKT": "Asia/Karachi", "SAST": "Africa/Johannesburg",
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_ISO = re.compile(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})")
_DMY_TEXT = re.compile(r"(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon>[A-Za-z]{3,9})\.?,?\s+(?P<y>\d{4})")
_MDY_TEXT = re.compile(r"(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})")
_DMY_NUM = re.compile(r"(?P<d>\d{1,2})[/.-](?P<m>\d{1,2})[/.-](?P<y>\d{4})")
# Year-less dates are accepted ONLY with a weekday, and only when exactly one nearby year fits that weekday.
_WEEKDAY = r"(?P<wd>mon|tues|wednes|thurs|fri|satur|sun)day"
_WD_DM = re.compile(_WEEKDAY + r",?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon>[A-Za-z]{3,9})\b", re.I)
_WD_MD = re.compile(_WEEKDAY + r",?\s+(?P<mon>[A-Za-z]{3,9})\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b", re.I)
WEEKDAYS = {"mon": 0, "tues": 1, "wednes": 2, "thurs": 3, "fri": 4, "satur": 5, "sun": 6}
_TIME = re.compile(r"(?<![\d/.-])(?P<h>\d{1,2})[:.](?P<mi>\d{2})(?::\d{2})?\s*(?P<ap>[ap]\.?m\.?)?", re.I)
_IANA = re.compile(r"\b[A-Z][A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?\b")
_UTC_OFFSET = re.compile(r"(?:UTC|GMT)\s*(?P<s>[+-])\s*(?P<h>\d{1,2})(?::?(?P<m>\d{2}))?")
_ISO_OFFSET = re.compile(r"^\s*(?:Z\b|(?P<s>[+-])(?P<h>\d{2}):?(?P<m>\d{2})\b)")


@dataclass
class Deadline:
    due_at: str | None          # UTC ISO, or None when unknown
    timezone: str | None
    tz_assumed: bool
    time_assumed: bool
    raw: str
    local_display: str | None   # e.g. '31 Mar 2027, 23:59 Asia/Kolkata'
    year_inferred: bool = False


def _find_date(text: str, today: datetime):
    for pat in (_ISO, _DMY_TEXT, _MDY_TEXT, _DMY_NUM):
        for m in pat.finditer(text):
            g = m.groupdict()
            if "mon" in g:
                month = MONTHS.get(g["mon"][:3].lower())
                if month is None:
                    continue
            else:
                month = int(g["m"])
            try:
                datetime(int(g["y"]), month, int(g["d"]))
            except ValueError:
                continue
            return int(g["y"]), month, int(g["d"]), m.span(), False
    for pat in (_WD_DM, _WD_MD):
        for m in pat.finditer(text):
            month = MONTHS.get(m.group("mon")[:3].lower())
            if month is None:
                continue
            day, weekday = int(m.group("d")), WEEKDAYS[m.group("wd").lower()]
            fits = []
            for year in (today.year - 1, today.year, today.year + 1):
                try:
                    if datetime(year, month, day).weekday() == weekday:
                        fits.append(year)
                except ValueError:
                    pass
            if len(fits) == 1:
                return fits[0], month, day, m.span(), True
    return None


def _offset_label(sign: str, hours: int, minutes: int) -> tuple[tzinfo, str]:
    delta = timedelta(hours=hours, minutes=minutes)
    if sign == "-":
        delta = -delta
    label = f"UTC{sign}{hours:02d}:{minutes:02d}"
    return timezone(delta, label), label


def _detect_tz(after_time: str, rest: str) -> tuple[tzinfo, str] | None:
    m = _ISO_OFFSET.match(after_time)
    if m:
        if m.group("s"):
            return _offset_label(m.group("s"), int(m.group("h")), int(m.group("m")))
        return timezone.utc, "UTC"
    for m in _IANA.finditer(rest):
        try:
            return ZoneInfo(m.group(0)), m.group(0)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    m = _UTC_OFFSET.search(rest)
    if m:
        return _offset_label(m.group("s"), int(m.group("h")), int(m.group("m") or 0))
    for word in re.findall(r"\b[A-Z]{2,5}\b", rest):
        if word in TZ_ABBR:
            name = TZ_ABBR[word]
            return (timezone.utc if name == "UTC" else ZoneInfo(name)), name
    return None


def parse_deadline(text: str | None, default_tz: str = "UTC", today: datetime | None = None) -> Deadline:
    raw = (text or "").strip()
    unknown = Deadline(None, None, False, False, raw, None)
    if UNKNOWN_RE.match(raw):
        return unknown
    found = _find_date(raw, today or datetime.now(timezone.utc))
    if not found:
        return unknown
    year, month, day, (start, end), year_inferred = found
    remainder = raw[:start] + " " + raw[end:]

    hour, minute, time_assumed, after_time = 23, 59, True, ""
    tm = _TIME.search(remainder)
    if tm:
        hour, minute = int(tm.group("h")), int(tm.group("mi"))
        ap = (tm.group("ap") or "").lower().replace(".", "")
        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return unknown
        time_assumed = False
        after_time = remainder[tm.end():]
        remainder = remainder[:tm.start()] + " " + remainder[tm.end():]

    detected = _detect_tz(after_time, remainder)
    tz_assumed = detected is None
    if detected is None:
        try:
            detected = (ZoneInfo(default_tz), default_tz)
        except ZoneInfoNotFoundError:
            detected = (timezone.utc, "UTC")
    tz_obj, label = detected

    local = datetime(year, month, day, hour, minute, tzinfo=tz_obj)
    utc = local.astimezone(timezone.utc)
    return Deadline(
        due_at=utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        timezone=label,
        tz_assumed=tz_assumed,
        time_assumed=time_assumed,
        raw=raw,
        local_display=f"{local.day} {local.strftime('%b %Y, %H:%M')} {label}",
        year_inferred=year_inferred,
    )


def format_local(due_at: str | None, tz_name: str | None) -> str | None:
    """Render a stored UTC instant in the notice's own timezone."""
    if not due_at:
        return None
    utc = datetime.strptime(due_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    tz: tzinfo = timezone.utc
    label = tz_name or "UTC"
    if tz_name and tz_name != "UTC":
        m = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", tz_name)
        if m:
            tz = _offset_label(m.group(1), int(m.group(2)), int(m.group(3)))[0]
        else:
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                label = "UTC"
    local = utc.astimezone(tz)
    return f"{local.day} {local.strftime('%b %Y, %H:%M')} {label}"


# ---------- classification helpers (keyword based; they describe text, they never decide eligibility)

# "Post-matric" (India) covers class 11 up to postgraduate; "pre-matric" covers school classes up to 10.
_LEVELS = [
    ("School", r"\bschools?\b|secondary|high school|class (?:x|xi|xii|\d+)|pre[- ]?matric|post[- ]?matric|means.cum.merit"),
    ("Diploma", r"diploma|polytechnic"),
    ("Undergraduate", r"under-?grad|under graduate|bachelor|\bug\b|-ug\b|\bb\.?sc\b|\bb\.?tech\b|first degree"
                      r"|technical degree|college|university students|post[- ]?matric|higher education|top class education(?! in schools)"),
    ("Postgraduate", r"post-?grad|post graduate|master|\bm\.?sc\b|\bm\.?tech\b|\bmba\b|\bpg\b|-pg\b|\(pgs\)|\bgraduate"
                     r"|post[- ]?matric|higher education"),
    ("Doctoral", r"ph\.?d|(?<!post)(?<!post-)doctora|doctor of|junior research fellow|\bjrf\b"),
    ("Postdoctoral", r"post-?doc"),
    ("Professional", r"faculty|mid-career|\bprofessionals?\b|teachers?\b|teaching|administrators?\b|educators?\b"),
]


def normalize_level(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t).lower()
    found = [name for name, pat in _LEVELS if re.search(pat, blob)]
    return ", ".join(found) if found else "Unspecified"


_CATEGORIES = [
    ("Need-based", r"need|income|financial hardship|means-tested|low-income|welfare"),
    ("Merit", r"merit|academic excellence|topper|outstanding|talent"),
    ("Research", r"research (?:grant|funding|fellowships?)|thesis|research fellow"),
    ("Travel", r"travel"),
    ("Fully funded", r"fully[- ]funded|full scholarship|full funding"),
    ("Fellowship", r"fellowship"),
]


def normalize_category(funding_text: str | None, *fallback: str | None) -> str:
    for blob in (funding_text, " ".join(t for t in fallback if t)):
        low = (blob or "").lower()
        for name, pat in _CATEGORIES:
            if re.search(pat, low):
                return name
    return "Other"


def notice_type_of(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t).lower()
    if "fellowship" in blob:
        return "fellowship"
    if re.search(r"\bgrant\b", blob):
        return "grant"
    return "scholarship"


_KEYWORDS = {
    "citizenship": r"citizen|nationals?\b",
    "residency / domicile": r"resident|domicile",
    "age limit": r"\bage\b|years? (?:old|of age)|(?:below|under) \d+",
    "minimum marks / GPA": r"gpa|cgpa|percent|%|marks|grade",
    "income limit": r"income",
    "gender-specific": r"women|female|girls?\b",
    "disability": r"disab|specially abled",
    "first-generation": r"first.generation",
    "minority / reserved category": r"minority|sc/st|\bobcs?\b|\bscs?\b students|\bscs\b|\bst students|scheduled|reserved"
                                    r"|\bebc\b|\bdnt\b",
    "enrolment required": r"enrol|admitted|admission",
    "language test": r"ielts|toefl",
}


def eligibility_keywords(*texts: str | None) -> list[str]:
    blob = " ".join(t for t in texts if t).lower()
    return [name for name, pat in _KEYWORDS.items() if re.search(pat, blob)]


# Broad subject groups so titles like "Joint Master in Sustainable Catalysis" can be filtered by subject.
_DISCIPLINES = [
    ("Computer science & AI", r"comput|software|\bdata\b|artificial intelligence|\bai\b|cyber|informatics|machine learning|robot"),
    ("Health & medicine", r"health|medic|pharma|clinical|nutrition|neuro|biomedic|nursing|disease|vaccin"),
    ("Agriculture & food", r"agri|food|crop|soil|insect|aquacult|animal|veterin|plant|\bicar\b|forestry"),
    ("Physics, chemistry & maths", r"physic|chemi|catalys|math|astro|quantum|radiation|statist|photon"),
    ("Engineering & technology", r"engineer|mechatron|aeronaut|electric|electronic|materials|nano|transport|construction"
                                 r"|manufactur|\bspace\b|technical|technolog"),
    ("Energy & environment", r"energy|renewable|climate|environment|sustainab|ecolog|biodiversity|water|marine|ocean|coast"),
    ("Life sciences", r"\bbio|genetic|molecul|life science"),
    ("Business & economics", r"business|econom|management|finance|entrepreneur|tourism|innovation|\bwork\b"),
    ("Law, politics & society", r"\blaw\b|legal|politic|policy|governance|human rights|peace|security|international relations"
                                r"|migration|gender|social|sociolog|development|leadership"),
    ("Humanities, languages & arts", r"histor|heritage|philosoph|language|linguist|multilingual|literat|cultur|\barts?\b"
                                     r"|music|film|media|journalism|archaeolog|architect|theolog|classic"),
    ("Education & teaching", r"educat|teach|pedagog"),
]


def infer_discipline(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for name, pat in _DISCIPLINES:
        if re.search(pat, blob):
            return name
    return "All subjects / interdisciplinary"


WITHDRAWN_RE = re.compile(r"withdrawn|cancel+ed|no longer (?:accepting|available)|discontinued", re.I)


def is_withdrawn(*texts: str | None) -> bool:
    return any(t and WITHDRAWN_RE.search(t) for t in texts)


def clean(text: str | None, limit: int | None = None) -> str | None:
    if text is None:
        return None
    out = re.sub(r"\s+", " ", text).strip()
    if not out:
        return None
    return out[:limit] if limit else out


def content_hash(fields: dict) -> str:
    """Hash of what matters to a student. Re-formatted but identical content hashes the same."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
