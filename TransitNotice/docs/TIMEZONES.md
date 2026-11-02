# Time-zone handling (acceptance test: "documented and reproducible")

1. **Storage is UTC only**, formatted `YYYY-MM-DDTHH:MM:SSZ`. The fixed width means string order equals time order, so SQL compares windows with plain `<` and `>`.
2. **Timestamps with an offset** (MBTA: `2026-11-02T06:00:00-05:00`) are converted to UTC directly.
3. **Naive timestamps** (Sample Metro: `2026-11-05 22:00`) are interpreted in the source's configured IANA zone (`sources.timezone`) using `zoneinfo`, so daylight-saving changes are handled. On Windows this needs the `tzdata` package.
4. **Date-only values** start at 00:00 for `valid_from` and end at 23:59:59 for `valid_to`, in the source zone.
5. **"TBD", "until further notice", empty** → `NULL` (open-ended).
6. **Display.** Times are rendered in the agency's own zone with its abbreviation (EST/EDT, IST), because that is the local time passengers care about.
7. **Active status** is calculated at request time by comparing the window with the current UTC time (`ingest.effective_status`, `queries.STATUS_SQL`). It is never inferred from fetch success.
8. **Travel-day filter.** `date=YYYY-MM-DD` selects advisories whose window overlaps that calendar day in `TN_DISPLAY_TZ` (default `Asia/Kolkata`). For example, 3 Nov means 2 Nov 18:30 UTC to 3 Nov 18:29:59 UTC. The drop-down offers today plus the next 6 days in that zone.
9. **CMRL** publishes date-only start dates ("March 30, 2025"), which are stored as 00:00 IST, i.e. `2025-03-29T18:30:00Z`.

Reproduce with:

```bash
python -m unittest tests.test_transitnotice.AcceptanceTests.test_timezone_handling -v
```

| Input | Zone | Stored |
|---|---|---|
| `2026-11-05 22:00` | Asia/Kolkata | `2026-11-05T16:30:00Z` |
| `2026-11-05 22:00` | America/New_York (EST) | `2026-11-06T03:00:00Z` |
| `2026-06-05 22:00` | America/New_York (EDT) | `2026-06-06T02:00:00Z` |
| `2026-11-05` as valid_to | Asia/Kolkata | `2026-11-05T18:29:59Z` |
