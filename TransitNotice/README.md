# TransitNotice — Public Transport Service Update Tracker (WEB-07)

TransitNotice monitors official public-transport advisories. It pulls out the affected routes, stations, areas, advisory type and effective window, and stores every version of each notice. It publishes a searchable dashboard that always shows **when each notice was last verified and where it came from**. A source that fails is shown as stale or erroring, never as live.

| | |
|---|---|
| Live source 1 (Tamil Nadu) | **CMRL road diversions**: 43 official Phase 2 traffic-diversion notices from chennaimetrorail.org (corridors 3, 4, 5) |
| Live source 2 (Tamil Nadu) | **CMRL press releases**: about 260 passenger-service announcements (closures, timing changes, maintenance, special trains, parking and tickets) from 2011 to today, tagged with Blue/Green Line and stations |
| Demo source | "Sample Chennai Transit", a fictional agency page served by this app, which mentors can change live |
| Private operators | Any operator that publishes an RSS/Atom feed can be added in `agencies.json`, with no code change. "Sample Private Coaches" is a clearly labelled demo private operator. |
| Optional source | MBTA (Boston) alerts API. Disabled in `agencies.json` |
| Stack | Python 3.11+, Flask, SQLite (stdlib `sqlite3`, `urllib`, `html.parser`, `xml.etree`, `zoneinfo`) |

**Filters.**
- All filters are **drop-down lists**: operator type (Government/Private), agency, route/corridor, station/location, area, kind of notice, show (current/ended), travel day, year, end date (announced / not announced), plus a sort order.
- By default the dashboard shows **everything**: notices in effect now first, then upcoming, then ended ones, each clearly labelled. Results come 30 at a time with a "Show 30 more" button.
- **Every option always has results.** Each list shows only the values that still match your other choices, with the number of notices next to them, e.g. "Corridor 4 (10)".
- Choosing an option updates the page immediately, and each active filter appears as a chip you can remove with one click.
- Text search is optional and hidden by default. Alert sign-up uses tick boxes.

## Adding an agency (government or private)

Add an entry to `agencies.json` and restart:

```json
{ "key": "my_operator", "adapter": "rss", "agency": "My Operator Pvt Ltd", "operator_type": "Private",
  "url": "https://example.com/service-notices.rss", "timezone": "Asia/Kolkata", "refresh_seconds": 1800,
  "area": "Chennai", "enabled": true }
```

How the RSS adapter reads each feed item:
- Routes come from `<category>`.
- Dates come from phrases like "from 05 Nov 2026 21:00 to 06 Nov 2026 05:00", "on 05 Nov 2026" or "until 10 Nov 2026".
- A title starting with "Cancelled:" marks the notice as cancelled.

Check the operator's robots.txt and terms first, and record them in `docs/SOURCE_APPROVAL.md`.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # then set TN_ADMIN_TOKEN and TN_CONTACT
python run.py serve             # http://127.0.0.1:5000
```

The scheduler starts with the web server. The first refresh runs about 5–10 s after start-up. After that each source refreshes on its own interval: CMRL every 6 h, the demo source every 5 min.

The first CMRL refresh downloads each of the ~43 diversion pages once, at 1 request/s, which takes about a minute. Later refreshes only re-download pages that CMRL has modified (the cache is `data/cmrl_detail_cache.json`).

Other commands:

```bash
python run.py refresh                 # one manual refresh of every source (server must be running for sample_metro)
python -m unittest discover -s tests -v
```

Force a refresh from the running app (admin only):

```bash
curl -X POST -H "X-Admin-Token: <TN_ADMIN_TOKEN>" http://127.0.0.1:5000/admin/refresh
```

## Pages

| URL | Purpose |
|---|---|
| `/` | Dashboard: freshness per source, filters (text, route, station, area, type, service date, status), charts, notice cards |
| `/?status=archive` | Expired, withdrawn and cancelled advisories |
| `/notice/<id>` | Full provenance and revision history |
| `/status` | Source health, backoff state, last 30 fetch runs |
| `/subscribe` | Alert sign-up (email + filter) |
| `/admin/alerts?token=…` | Alert delivery log (admin) |
| `/api/notices?route=Red&status=current` | JSON API (same filters as the dashboard) |

## Mentor live challenges (Sample Metro)

```bash
python run.py demo reset               # fresh data relative to "now"
python run.py demo extend SM-101 24    # CP-5 / challenge 1: extend outage by 24 h -> revision 2, "Extended: ..."
python run.py demo open-ended          # challenge 2: advisory without an end date -> ACTIVE + "No end date announced"
python run.py demo withdraw SM-102     # notice disappears from source -> WITHDRAWN (kept in archive)
python run.py demo cancel SM-103       # source marks it cancelled -> CANCELLED
python run.py demo break               # CP-6 failure simulation: layout change -> SOURCE ERROR, data kept, backoff
python run.py demo corrupt             # malformed HTML + missing fields + duplicates -> bad items skipped & logged
python run.py demo normal              # back to a normal page
```

After each command, wait for the next scheduled run (at most 5 min) or POST `/admin/refresh`.

## Project layout

```
run.py                       CLI: serve | refresh | demo
migrations/001_init.sql      schema (constraints, unique keys, indexes)
transitnotice/
  config.py                  env/.env configuration (no secrets in code)
  timeutil.py                time-zone policy (UTC storage)
  db.py                      connection + forward-only migrations
  sources/base.py            polite fetcher: robots.txt, rate limit, timeout, retry/backoff
  sources/cmrl.py            Chennai Metro (Tamil Nadu) adapter: WordPress list API + HTML detail pages
  sources/cmrl_press.py      Chennai Metro press releases -> passenger service notices
  sources/feed.py            generic RSS/Atom adapter (private operators, configured in agencies.json)
  sources/mbta.py            MBTA JSON adapter (optional)
  sources/sample_metro.py    tolerant HTML adapter
  sources/normalize.py       advisory-type normalisation
  ingest.py                  idempotent upsert, revisions, withdrawal, freshness, failure handling
  alerts.py                  one alert per (notice, revision, subscriber); demo/SMTP delivery
  scheduler.py               background scheduler honouring per-source next_run_at
  queries.py, app.py         search + Flask UI / JSON API
agencies.json                list of agencies (government / private), enabled flags
tests/                       59 unit/integration tests + fixtures
docs/                        source approval, time zones, final report
```

Documentation: [docs/SOURCE_APPROVAL.md](docs/SOURCE_APPROVAL.md) · [docs/TIMEZONES.md](docs/TIMEZONES.md) · [docs/REPORT.md](docs/REPORT.md) · [docs/report.pdf](docs/report.pdf)
