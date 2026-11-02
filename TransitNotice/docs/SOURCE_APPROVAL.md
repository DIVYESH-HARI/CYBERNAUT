# Source approval document (CP-1 / CP-2)

## 1. Sources

| Key | Source | Access method | Why it is permitted |
|---|---|---|---|
| `cmrl` (**default, Tamil Nadu**) | Chennai Metro Rail Limited. List: `https://chennaimetrorail.org/wp-json/wp/v2/road-diversion` (+ `/rd-category`). Detail: each notice page `https://chennaimetrorail.org/road-diversion/<slug>/` | The site's public WordPress REST endpoint + public HTML pages | `robots.txt` (checked 02 Nov 2026) is `User-agent: * / Disallow:` with an empty value, so everything is allowed. Pages carry `meta robots: index, follow`. The data is official public traffic-diversion information. |
| `cmrl_press` (**default, Tamil Nadu**) | CMRL press releases: `https://chennaimetrorail.org/press-release/` (one page, all years). Station → line list: `/wp-json/wp/v2/station-information` | Public HTML table + public WordPress REST list | Same site and same robots.txt as above. 2 requests per refresh (every 6 h). PDFs are linked, not downloaded. |
| `sample_metro` (default) | `http://127.0.0.1:5000/demo-source/notices.html` | HTML page served by this project | A fictional "Sample Chennai Transit" agency created for the demo. Its `robots.txt` explicitly allows `/demo-source/`. |
| `mbta` (optional) | MBTA V3 API: `https://api-v3.mbta.com/alerts` (+ `/stops`) | Public JSON API | Published for developers. `robots.txt` returns 404. Anonymous use is allowed at low rates. |

**Other Tamil Nadu sources checked on 02 Nov 2026 and not used:**

| Source | Finding | Decision |
|---|---|---|
| Southern Railway (sr.indianrailways.gov.in) | `robots.txt` returned an error page, so the crawl policy cannot be read | Not crawled. Under our policy an unreadable robots.txt means deny. |
| MTC Chennai (mtcbus.tn.gov.in) | TLS certificate chain could not be verified | Not crawled. We do not disable certificate checks. |
| TNSTC (tnstc.in) | No robots.txt (404) and no advisory listing found | Could be added after a terms review |

**Private Tamil Nadu operators checked on 02 Nov 2026:**

| Operator | Finding | Decision |
|---|---|---|
| KPN Travels | Ticket-booking web app; no notice page or feed | Nothing to collect |
| YoloBus | Single-page web app; no notices | Nothing to collect |
| IntrCity SmartBus | Every request, including robots.txt, returns 403 to bots | Not crawled; they block bots |
| Freshbus | robots.txt blocks several crawlers; no feed | Not crawled |
| Zingbus | robots.txt blocks several crawlers; no feed | Not crawled |
| Parveen Travels | No robots.txt, feed or notice listing | Nothing to collect |
| Namma Yatri | No robots.txt, feed or notice listing | Nothing to collect |
| SRS Travels | Domain does not resolve | Not reachable |

**Conclusion.** No private operator currently publishes service notices in a form we may collect. The system is ready for them:
- `agencies.json` takes an RSS/Atom feed URL and `operator_type: Private`.
- The fictional "Sample Private Coaches (demo operator)" demonstrates the whole path: feed, parse, store, Private filter, alerts.
- We do not invent or copy notices for real private companies.

**CMRL crawl budget:**
- A normal refresh every 6 h makes 2 list requests, plus one request for each page whose `modified_gmt` changed.
- The first run makes about 45 requests at 1 per second.
- Detail requests have a 30 s timeout and up to 3 attempts.

**CMRL terms.** Before deployment, read the website policy and terms pages linked from chennaimetrorail.org's footer, and record the review date here: `____`.

**Before deployment, review the MBTA terms.** Read the developer terms at <https://www.mbta.com/developers> and record the review date here: `____`. If you need more than the anonymous rate limit, request a free API key and pass it as `x-api-key` via an environment variable. Never hard-code it.

**Alternatives considered.** The MBTA GTFS-Realtime alerts feed (protobuf) carries the same data but needs an extra dependency. Scraping the human-readable mbta.com alerts pages was rejected because a sanctioned API exists. Other agencies (TfL Unified API, NJ Transit, Indian metro sites) could each be added as one more adapter. Their terms must be reviewed first. Several Indian metro websites publish advisories only as images or social-media posts, which is a known limitation.

## 2. Crawl policy

- Identify honestly. The User-Agent is `TransitNoticeBot/1.0 (student project; contact: <TN_CONTACT>)`.
- Check `robots.txt` before every new origin. A 404 means allowed. An unreachable file, 5xx, 401 or 403 means **deny** (conservative).
- Rate limit: at least 1 s between requests to the same host. Refresh every 15 min (MBTA) or 5 min (demo).
- Timeout 15 s. At most 3 attempts per refresh, with exponential backoff (2 s, 4 s). Retry only on network errors, 429 and 5xx.
- After failed refreshes, the next run is pushed back 10 min, 20 min, 40 min and so on (`300 s × 2^failures`), capped at 6 h.
- Never log in, bypass a CAPTCHA, or fetch anything that is not publicly listed.
- **Data minimisation.** Advisories contain no personal data. The only personal data stored is a subscriber's email address and filter, collected with consent on `/subscribe`.

## 3. Field dictionary

### CMRL (Chennai Metro, Tamil Nadu)

| Our field | CMRL source | Notes |
|---|---|---|
| notice_id | `"cmrl:" + id` (REST list) | Stable WordPress post id |
| route_id | `rd-category` → `corridor-3/4/5` → "Corridor 3/4/5" | Phase 2 corridors |
| area | "Chennai - Corridor N (endpoints)" | 3 = Madhavaram - SIPCOT, 4 = Light House - Poonamallee, 5 = Madhavaram - Sholinganallur |
| stations | REST `title.rendered` (HTML-unescaped) | Diversion location, e.g. "Otteri" |
| type | constant `detour` | Every notice is a traffic diversion |
| summary | page `h1.h1` + text of `<p>`/`<li>` in `div.description` + "CMRL Package" card | |
| published_at | REST `date_gmt` | UTC |
| valid_from | "Diversion Start Date" info card (e.g. "March 30, 2025") | Date only; start of day, IST |
| valid_to | none published | NULL means open-ended, shown as "No end date announced". When CMRL removes a page, the notice becomes *withdrawn*. |
| source_url | REST `link` | Official page |
| change detection | REST `modified_gmt` (re-download trigger) + our content hash | Page also shows "Last Updated On" |

### CMRL press releases

| Our field | Source | Notes |
|---|---|---|
| notice_id | `"cmrl_press:" + PDF file name` (hash of date + title when there is no PDF) | Stable per release |
| keep / skip | Title keywords | Kept: passenger service (closures, timings, frequency, maintenance, special trains, parking, tickets…). Skipped: contracts, tenders, ridership figures, TBM/milestones, inaugurations, trials. |
| type | Title keywords | Closure → station_closure; maintenance/track → construction; amenities/parking/tickets → other; timings/special trains → schedule_change |
| route_id | "Blue Line" / "Green Line" / "Corridor N" in the title, plus the line of any station named | Station → line from CMRL's station list. Otherwise "All Chennai Metro lines". |
| stations | CMRL station names (and short aliases, e.g. "Central") found in the title | |
| published_at | Date column (dd/mm/yyyy, dd.mm.yyyy) | IST |
| valid_from / valid_to | Dates in the title, e.g. "between 20.10.2025 and 24.10.2025", "January 14, 15 and 16", "5th January 2025". "Permanent …" means open-ended. Otherwise **14 days after release** (documented heuristic: releases carry no end date). | A date without a year is taken as the next such date after the release |
| source_url | PDF link (or the press-release page) | |

### MBTA and Sample Chennai Transit

| Our field | MBTA source | Sample Metro source | Notes |
|---|---|---|---|
| notice_id | `"mbta:" + data[].id` | `"sample_metro:" + article@data-id` | Stable primary key |
| agency | constant | constant | |
| route_id | distinct `informed_entity[].route` | `ul.routes > li` | Comma-separated. Also stored one row per route in `notice_routes` |
| stations | `informed_entity[].stop` (parent `place-*`), names via `/stops` | `.stations` split on `;` | `notice_stations` |
| area | "Greater Boston - " + route types | `.area` | |
| type | `effect`, mapped by `normalize.MBTA_EFFECT` | `.type`, keyword mapping | One of 8 normalised types |
| summary | `header` | `.title` + " - " + `.summary` | Whitespace-normalised |
| published_at | `created_at` | `time.published` | UTC |
| valid_from | min(`active_period[].start`) | `time.from` | UTC |
| valid_to | max(`active_period[].end`); NULL if any period is open | `time.to` ("TBD"/empty → NULL) | NULL = open-ended |
| status (source) | `lifecycle == CANCELLED` → cancelled | `article@data-status` | Absence from the listing → withdrawn |
| source_url | `api-v3.mbta.com/alerts/<id>` | `a.more@href` or `page#id` | Provenance link |

## 4. Error strategy

| Situation | Behaviour |
|---|---|
| Network error, timeout, 5xx, 429 | Retry up to 3× with backoff. Then the run is recorded as `error`, the source shows **SOURCE ERROR**, notices are not touched, and the next run is backed off. |
| 4xx (other than 429) | No retry. Recorded as error. |
| robots.txt disallows | `RobotsDenied`. Recorded as error and nothing is fetched. |
| Page has no notices and no `data-empty="true"` marker (layout change or error page) | `ParseError`. **Nothing is withdrawn**, because an empty parse must never delete live data. |
| Single item malformed (no id, no text, no route/area, backwards dates, duplicate id) | Item skipped. A diagnostic goes to `fetch_runs.error`, and the other items are still stored. |
| All items malformed | Treated as a failed run. |
| CMRL: one detail page times out or changes layout | That notice is skipped, kept as-is and **not** re-verified (`last_confirmed_at` unchanged). It is never withdrawn while CMRL still lists it. Pages already downloaded stay cached. |
| CMRL: list API empty or not JSON | ParseError, so nothing is withdrawn. |
| Alert delivery fails | Logged as `failed` in `alert_log`. Ingestion is unaffected. |

## 5. Schedule

The scheduler wakes every 30 s and runs each source whose `next_run_at` is due. Admins can trigger a manual refresh at `POST /admin/refresh` or with `python run.py refresh`.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Source changes format | Sanity checks raise ParseError, and parser tests use fixtures. The failure is visible on `/status`. |
| Stale data mistaken for current | Every card shows "last verified", and a source banner shows live, stale or error. "Live" only appears after a recent successful fetch. |
| Terms change or API key becomes required | Re-review terms each semester. The key is supplied via env. |
| Alert spam | UNIQUE (notice, revision, subscriber), and no alerts for advisories that were already expired when first seen. |
| Time-zone errors | UTC storage plus per-source IANA zone, covered by tests (see TIMEZONES.md). |
