# Source Approval Document (Task 01 / Checkpoint CP-1)

> **Status: DRAFT - mentor sign-off needed.** The four official sources below were checked on **21 Oct 2026**
> (robots.txt, copyright/terms pages, page structure) and are enabled in `sources.json`. Two of them
> (NSP and CSC) state that their content is copyright / may not be reproduced without permission. The app
> therefore stores **facts only** (scheme name, ministry/provider, study level, dates) plus a **link back**,
> and copies no paragraphs. Before any public deployment, get the mentor's approval and - for NSP - consider
> writing to `helpdesk@nsp.gov.in` for permission. To switch a source off, set `"enabled": false` in `sources.json`.

## Approval checklist (applied to every source)

- [x] Page is public: no login, no CAPTCHA, no paywall, no personal data.
- [x] `robots.txt` read; the listing URL is not disallowed for `*` (results below).
- [x] Terms / copyright page read (results below).
- [x] Looked for an official API or RSS feed first (none found for these listings).
- [ ] Contact details added to `SCHOLARTRACK_USER_AGENT` so site owners can reach you. **(student to do)**
- [ ] Mentor approval recorded (name, date). **(student to do)**

## 1. National Scholarship Portal (Government of India)

| Item | Entry |
|---|---|
| Owner | National Scholarship Portal (NIC / Ministry of Electronics & IT), Government of India |
| Listing URL | https://scholarships.gov.in/All-Scholarships |
| Layout / adapter | `blocks` - one block per scheme, grouped by ministry, with "Student Application Open till : dd-mm-yyyy" badges |
| robots.txt (21 Oct 2026) | `/robots.txt` returns 404 - no published crawl rules |
| Terms reviewed | Copyright Policy: content "may not be reproduced partially or fully, without due permission"; if referred to by another website "the source must be appropriately acknowledged". Terms: information is for reference only. |
| Conclusion | Store facts only (scheme name, ministry, deadline, level) with prominent source acknowledgement and a link to the official guideline PDF. **Mentor decision needed; ask NSP for permission before any public deployment.** |
| Fields collected | scheme title, ministry, student-application closing date, renewal-only flag, guideline-PDF link |
| Schedule | every 6 h; 1 request per run |
| Coverage | ~31 central schemes: school, diploma, UG, PG, research fellowships |

## 2. USIEF - Fulbright-Nehru Fellowships for Indian Citizens

| Item | Entry |
|---|---|
| Owner | United States-India Educational Foundation |
| Listing URL | https://www.usief.org.in/fulbright-fellowships/fellowships-for-indian-citizen/ |
| Layout / adapter | `detail_pages` - index page links to one page per fellowship; deadline read from the "Timeline" table row labelled "Application deadline" |
| robots.txt (21 Oct 2026) | `User-agent: * / Allow: / / Disallow: /staging` - allowed |
| Terms reviewed | Disclaimer page found, no prohibition on automated access found |
| Conclusion | Facts + link only; no page text stored |
| Schedule | every 6 h; ~9 requests per run, 2 s apart |
| Coverage | ~8 fellowships (doctoral, postdoctoral, professional, teaching) |

## 3. Commonwealth Scholarship Commission in the UK

| Item | Entry |
|---|---|
| Owner | Commonwealth Scholarship Commission in the UK (FCDO-funded) |
| Listing URL | https://cscuk.fcdo.gov.uk/scholarships/ |
| Layout / adapter | `detail_pages` - deadline read from the sentence "The closing date for applications is ..." |
| robots.txt (21 Oct 2026) | `User-agent: * / Disallow:` (empty) - allowed |
| Terms reviewed | Copyright notice: "All rights reserved"; information "should not be regarded as definitive" |
| Conclusion | Facts + link only; the page summary is used to classify the study level but is **not stored** |
| Schedule | every 6 h; ~6 requests per run, 2 s apart |
| Note | CSC writes some dates without a year ("Tuesday 20 October"). The year is only filled in when exactly one year near today has that weekday, and the notice is flagged "year worked out from the weekday". |

## 4. European Commission (EACEA) - Erasmus Mundus Catalogue

| Item | Entry |
|---|---|
| Owner | European Education and Culture Executive Agency (EU) |
| Listing URL | https://www.eacea.ec.europa.eu/scholarships/erasmus-mundus-catalogue_en |
| Layout / adapter | `cards` with pagination (`?page=N`, up to 20 pages) |
| robots.txt (21 Oct 2026) | Standard Drupal rules; the catalogue path is not disallowed |
| Terms reviewed | Commission legal notice: EU-owned content is licensed under Creative Commons Attribution 4.0 (reuse with attribution) |
| Conclusion | Approved for reuse with attribution. Each programme links to its own official website. |
| Schedule | once a day (the catalogue is updated yearly); ~12 requests per run, 2 s apart |
| Limitation | The catalogue publishes no per-programme deadline, so these show "deadline not published" with the catalogue's note (most apply October-January). |

## Sources considered and not used

| Source | Reason |
|---|---|
| DAAD scholarship database | Results load through JavaScript (a hidden API); would need separate permission check |
| Chevening | Single global deadline on a timeline page; could be added later |
| Buddy4Study and other aggregators | Not official sources; terms restrict scraping |

## Built-in demo sources (fictional - only with `start-demo.bat`)

| Source | URL (local) | Layout | Purpose |
|---|---|---|---|
| Demo National Scholarship Portal | `/demo-source/portal-a` | table | Mentor challenges: timezone change, revision, withdrawal |
| Demo International Fellowship Board | `/demo-source/portal-b` | cards | Mentor challenge: unseen failure (portal offline / layout change) |

Crawl policy applied to all sources: obey robots.txt (401/403 = disallow, 5xx = skip and flag), at least 2 s between
requests to the same host, 20 s timeout, at most 3 tries with exponential backoff, no login/CAPTCHA handling, no
redirects to other hosts, 2 MB response cap, HTML/text only, User-Agent identifies the project.
