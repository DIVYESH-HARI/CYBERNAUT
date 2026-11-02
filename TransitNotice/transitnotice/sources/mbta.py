"""Adapter: MBTA V3 public alerts API (https://api-v3.mbta.com/docs). Open JSON feed intended for developers;
used without a key at the free anonymous rate limit with a descriptive User-Agent."""
import json
from urllib.parse import quote

from ..timeutil import parse_dt
from .base import FetchResult, ParseError, ParsedNotice
from .normalize import clean_text, normalize_type

ROUTE_TYPE = {0: "Light rail", 1: "Subway", 2: "Commuter rail", 3: "Bus", 4: "Ferry"}


class MbtaAdapter:
    source_key = "mbta"
    agency = "MBTA (Massachusetts Bay Transportation Authority)"
    timezone = "America/New_York"
    refresh_seconds = 900
    api = "https://api-v3.mbta.com"

    def __init__(self, base_url=None, route_types="0,1,2"):
        self.base_url = base_url or self.api
        self.route_types = route_types

    @property
    def alerts_url(self):
        return "%s/alerts?filter[route_type]=%s&page[limit]=250" % (self.base_url, self.route_types)

    def fetch(self, fetcher):
        status, body, _ = fetcher.get(self.alerts_url)
        doc = _load(body)
        names, diag = {}, []
        ids = sorted({e["stop"] for a in doc["data"] for e in a["attributes"].get("informed_entity", [])
                      if str(e.get("stop", "")).startswith("place-")})
        for i in range(0, len(ids), 50):
            try:
                _, sbody, _ = fetcher.get("%s/stops?filter[id]=%s&fields[stop]=name"
                                          % (self.base_url, quote(",".join(ids[i:i + 50]), safe=",")))
                for s in json.loads(sbody)["data"]:
                    names[s["id"]] = s["attributes"]["name"]
            except Exception as e:  # station names are best-effort; ids remain usable
                diag.append("stop-name lookup failed: %s" % e)
                break
        notices, skipped, d2 = parse_alerts(doc, names, self.timezone, self.base_url)
        return FetchResult(notices, skipped, diag + d2, status)


def _load(body):
    try:
        doc = json.loads(body)
    except ValueError as e:
        raise ParseError("alerts feed is not JSON: %s" % e) from e
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
        raise ParseError("alerts feed missing 'data' list")
    return doc


def parse_alerts(doc, stop_names, tz, base_url="https://api-v3.mbta.com"):
    out, skipped, diag, seen = [], 0, [], set()
    for a in doc["data"]:
        try:
            at = a["attributes"]
            aid = str(a["id"])
            summary = clean_text(at.get("header") or at.get("short_header") or "")
            if not summary:
                raise ValueError("no header text")
            if aid in seen:
                raise ValueError("duplicate id")
            seen.add(aid)
            routes, stations, rtypes = [], [], set()
            for e in at.get("informed_entity", []):
                if e.get("route") and e["route"] not in routes:
                    routes.append(e["route"])
                if e.get("route_type") is not None:
                    rtypes.add(ROUTE_TYPE.get(e["route_type"], str(e["route_type"])))
                stop = e.get("stop")
                if stop and str(stop).startswith("place-"):
                    nm = stop_names.get(stop, stop)
                    if nm not in stations:
                        stations.append(nm)
            periods = at.get("active_period") or []
            starts = [parse_dt(p.get("start"), tz) for p in periods if p.get("start")]
            ends = [parse_dt(p.get("end"), tz) for p in periods]
            valid_from = min(starts) if starts else None
            # open-ended if any period has no end, else the latest end
            valid_to = None if (not periods or any(e is None for e in ends)) else max(ends)
            life = (at.get("lifecycle") or "").upper()
            out.append(ParsedNotice(
                external_id=aid, summary=summary, type=normalize_type(at.get("effect"), summary),
                route_ids=routes, stations=stations,
                area="Greater Boston - " + ", ".join(sorted(rtypes)) if rtypes else "Greater Boston",
                published_at=parse_dt(at.get("created_at"), tz), valid_from=valid_from, valid_to=valid_to,
                source_url="%s/alerts/%s" % (base_url, aid),
                source_status="cancelled" if life == "CANCELLED" else "active"))
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            skipped += 1
            diag.append("skipped alert %r: %s" % (a.get("id") if isinstance(a, dict) else a, e))
    return out, skipped, diag
