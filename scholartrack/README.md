# ScholarTrack - Scholarship & Fellowship Deadline Tracker (WEB-02)

Collects **real** scholarship/fellowship announcements from official public sources, normalises eligibility and
deadlines, detects revisions/withdrawals, and shows a searchable, source-linked dashboard that never
labels a stale snapshot as live and never invents a deadline.

**Sources** (checked 21 Oct 2026 - see `docs/SOURCE_APPROVAL.md`, mentor sign-off pending):
National Scholarship Portal (Govt of India), USIEF Fulbright-Nehru, Commonwealth Scholarship Commission UK,
and the EU Erasmus Mundus catalogue - about 260 notices. Every notice links to its official page or guidelines.

**Stack:** Python 3.11+ - FastAPI - SQLite (SQL migrations) - httpx + BeautifulSoup/lxml - plain HTML/JS dashboard (no build step) - pytest.
Chosen because it needs no external services (one `pip install`, one command), the scraping libraries are mature, and SQLite gives real constraints and transactions for idempotent upserts.

## Quick start (Windows / macOS / Linux)

Double-click **`start.bat`** (Windows), or:

```bash
cd scholartrack
pip install -r requirements.txt
python run.py
```

The browser opens at <http://127.0.0.1:8000>. The terminal prints an **operator token**
(also saved in `data/operator_token.txt`, which is git-ignored). The first collection starts ~3 seconds after
start and takes about a minute (requests are spaced 2 s apart to be polite); the page fills in by itself.
After that each source is re-checked on its own interval (`refresh_minutes` in `sources.json`: 6 h, catalogue daily).

**No internet?** The app ships with a snapshot of real notices collected on 21 Oct 2026 (`seed/snapshot.json`).
It is loaded only for sources that have no data yet, keeps its true verification time (so it is labelled stale as
it ages), and is updated by the first successful live check. If the official sites can't be reached, a red strip
says so, and **Try again now** re-checks them; failed sources are also retried automatically after 2, 4, 8 ... minutes.

**`start-demo.bat`** additionally enables two fictional demo sources and the mentor-challenge controls
(set `SCHOLARTRACK_DEMO=1` to do the same by hand).

### Guided walkthrough (for students, and for the live demo)
1. **Find notices** - tap the search box (or **Topics**) and pick a ready-made topic - each one shows how many
   scholarships it finds - or pick a study level / funding type, or tap a quick pick. Dropdowns only list values
   that exist, with how many are open. If no notice matches *every* filter, the closest matches are shown with a
   note saying exactly which filter was relaxed - the student never hits an empty page.
2. **Deadline timeline** - the same notices grouped by urgency, with text countdowns.
3. **Alerts** - one alert per meaningful change, with source link and verification state.
4. **Operator** tab - paste the token. You get source health, run history, delivery log and (with
   `start-demo.bat`) a **Live demo walkthrough** panel that edits the fictional demo sources:
   - *another timezone*: set deadline text like `20 Dec 2030, 10:00 Asia/Tokyo`, press **Refresh all now**
   - *revised notice*: the Alerts tab shows the revision, the notice card shows its change history
   - *withdraw/restore*, *add a notice*, *take portal B offline*, *change portal A layout* (parser failure)

## Run the tests

```bash
python -m pytest -q
```

78 tests cover deadline/timezone parsing (incl. dd-mm-yyyy and weekday-only dates), all four layout adapters on
fixtures that mimic the real sites (malformed HTML, missing fields, duplicates, layout change, pagination, an
unreadable detail page), the 001->002 migration, idempotent persistence, change detection, one-alert-per-revision,
robots.txt, retry/backoff, source outage flagging, timezone-aware date filters, closest-match fallback and
operator access control. Tests never touch the internet.

## Configuration (environment variables - never put secrets in code)

| Variable | Default | Meaning |
|---|---|---|
| `SCHOLARTRACK_OPERATOR_TOKEN` | generated into `data/operator_token.txt` | Token for operator endpoints |
| `SCHOLARTRACK_STALE_MIN` | 3 x each source's interval | Age after which data is shown as **stale** |
| `SCHOLARTRACK_SCHEDULER` | `1` | `0` = manual refresh only |
| `SCHOLARTRACK_DEMO` | `0` | `1` adds the fictional demo sources and demo controls (`start-demo.bat`) |
| `SCHOLARTRACK_USER_AGENT` | ScholarTrackBot/1.0 ... | Set to include your contact email/URL |
| `SCHOLARTRACK_TIMEOUT` / `_RETRIES` / `_BACKOFF` / `_MIN_INTERVAL` | 20s / 3 / 1.0s / 2.0s | Fetch timeout, retry ceiling, backoff base, per-host delay |
| `SCHOLARTRACK_WEBHOOK_URL` | empty | Optional alert webhook; empty = safe demo mode (log only) |
| `SCHOLARTRACK_DB`, `SCHOLARTRACK_BASE_URL`, `SCHOLARTRACK_SOURCES` | `data/scholartrack.db`, `http://127.0.0.1:8000`, `sources.json` | Paths / URLs |

## Adding a real source (after approval)

Read `docs/SOURCE_APPROVAL.md` first: check robots.txt and terms, record the decision, then add an entry to
`sources.json`. Four layouts are supported:

| Adapter | Page shape | Used by | Key config |
|---|---|---|---|
| `table` | `<table>` rows, columns named by `<th>` | demo A | `table`, `labels` |
| `cards` | repeated cards with a heading link, optional `<dl>` | Erasmus Mundus, demo B | `card`, `title`, `next_selector`, `max_pages`, `fallback_to_listing` |
| `blocks` | blocks with "Label : value" badges, grouped by a heading | NSP | `block`, `title`, `group_class`, `badge_labels`, `link_text` |
| `detail_pages` | index page linking to one page per scholarship | CSC, USIEF | `link_pattern`, `exclude_pattern`, `deadline_rules` |

`defaults` fills facts the page does not state (e.g. `"residency_rule": "Indian citizens"`). A source can be
switched off with `"enabled": false`; its notices disappear from the dashboard but stay in the database.

## How it works

```
sources.json -> Fetcher (robots.txt, rate limit, timeout, retry/backoff)
             -> Adapter (table | cards | blocks | detail_pages, with pagination) -> RawNotice
             -> normalize (deadline+timezone, level, category, keywords)
             -> upsert (BEGIN IMMEDIATE, content hash, version)
             -> changes (deadline_revised / set / removed / withdrawn / reinstated / details_updated)
             -> alerts (UNIQUE per change)  -> delivery_log
FastAPI: /api/notices, /api/alerts, /api/status (public) - /api/operator/* (token)
```

Design rules that map to the brief's acceptance tests:
- Unknown or unreadable deadline -> `due_at` is `NULL`, raw text kept, shown as "not published". Date filters exclude it.
- Same source row with new content -> **update** (`version` + `changes` row), never a new notice (key = source id + page key/URL).
- `due_at` is stored as a UTC instant plus the source's zone; filters convert the viewer's day to UTC.
- Unreachable source / robots disallow / layout change -> the run is marked failed, source shows **needs review**, old notices are labelled "unreachable", not deleted.
- If only part of a source could be read (a later page or one detail page failed), the run is a **warning** and nothing is marked as removed.
- Every notice stores and displays the link to the original announcement.
- `UNIQUE(notice_id, version)` on `changes` and `UNIQUE(change_id)` on `alerts` => one alert per revision even if a refresh runs twice.

## Deployment and monitoring

- Any host with Python 3.11+: `pip install -r requirements.txt`, set `SCHOLARTRACK_OPERATOR_TOKEN`, `SCHOLARTRACK_DEMO=0`,
  `SCHOLARTRACK_BASE_URL`, then `uvicorn app.main:app --host 0.0.0.0 --port 8000` behind HTTPS (e.g. a reverse proxy). Run a single worker (the scheduler is in-process).
- Back up `data/scholartrack.db`. Migrations in `migrations/` are applied automatically at start-up.
- Monitor via the Operator tab: failed runs, consecutive failures, delivery log; and `/api/status` (`state`: fresh / stale / degraded / never).

## Known limits
Scheduled monitoring is not real-time; keyword tags are not eligibility decisions; timezone abbreviations such as
IST/CST are mapped to one common reading; date-only deadlines are treated as 23:59 in the source zone and flagged
"assumed"; Erasmus Mundus programmes publish deadlines only on their own sites (shown as "not published"); a few NSP
schemes do not state a study level ("Level not stated by source"); study subject is inferred from the title.
