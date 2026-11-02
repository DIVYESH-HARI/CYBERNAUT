"""Normalisation of advisory types and text."""
import re

TYPES = ("detour", "suspension", "delay", "station_closure", "schedule_change", "construction", "accessibility", "other")

_KEYWORDS = [
    ("station_closure", ("station closure", "stop closure", "station closed", "stop closed")),
    ("accessibility", ("elevator", "escalator", "accessib", "lift out")),
    ("suspension", ("suspend", "suspension", "no service", "shuttle", "outage", "service stopped", "not running")),
    ("detour", ("detour", "diversion", "diverted", "rerout", "re-rout")),
    ("delay", ("delay", "disruption", "cancel")),
    ("schedule_change", ("schedule", "timetable", "additional service", "extra service", "frequency")),
    ("construction", ("construction", "maintenance", "track work", "trackwork", "engineering work", "renovation")),
]

MBTA_EFFECT = {
    "DETOUR": "detour", "SHUTTLE": "suspension", "SUSPENSION": "suspension", "NO_SERVICE": "suspension",
    "STATION_CLOSURE": "station_closure", "STOP_CLOSURE": "station_closure", "DOCK_CLOSURE": "station_closure",
    "DELAY": "delay", "SIGNIFICANT_DELAY": "delay", "CANCELLATION": "delay",
    "SCHEDULE_CHANGE": "schedule_change", "SERVICE_CHANGE": "schedule_change", "ADDITIONAL_SERVICE": "schedule_change",
    "EXTRA_SERVICE": "schedule_change", "STOP_MOVE": "schedule_change", "STOP_MOVED": "schedule_change",
    "TRACK_CHANGE": "construction", "STATION_ISSUE": "other",
    "ELEVATOR_CLOSURE": "accessibility", "ESCALATOR_CLOSURE": "accessibility", "ACCESS_ISSUE": "accessibility",
}


def normalize_type(raw, summary=""):
    """Map a free-text/enum advisory label to one of TYPES; falls back to keyword search on summary."""
    key = (raw or "").strip()
    if key.upper() in MBTA_EFFECT:
        return MBTA_EFFECT[key.upper()]
    low = key.lower().replace("_", " ")
    if low in TYPES or low.replace(" ", "_") in TYPES:
        return low.replace(" ", "_")
    for t, words in _KEYWORDS:
        if any(w in low for w in words):
            return t
    low = (summary or "").lower()
    for t, words in _KEYWORDS:
        if any(w in low for w in words):
            return t
    return "other"


def clean_text(s):
    return re.sub(r"\s+", " ", (s or "").replace("\r", " ")).strip()


def norm_route(r):
    return clean_text(r)
