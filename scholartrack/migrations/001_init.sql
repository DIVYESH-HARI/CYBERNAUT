-- ScholarTrack schema v1. Applied once by app/db.py (tracked in schema_migrations).

CREATE TABLE sources (
    source_id            TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    listing_url          TEXT NOT NULL,
    adapter              TEXT NOT NULL CHECK (adapter IN ('table', 'cards')),
    default_timezone     TEXT NOT NULL DEFAULT 'UTC',
    enabled              INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    approved_by          TEXT,
    approved_at          TEXT,
    last_checked_at      TEXT,
    last_success_at      TEXT,
    last_status          TEXT NOT NULL DEFAULT 'never'
                         CHECK (last_status IN ('never', 'ok', 'warning', 'failed')),
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    config_json          TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE notices (
    notice_id            TEXT PRIMARY KEY,
    source_id            TEXT NOT NULL REFERENCES sources (source_id),
    external_key         TEXT NOT NULL,
    url                  TEXT NOT NULL,           -- original official announcement
    listing_url          TEXT NOT NULL,           -- page it was collected from
    notice_type          TEXT NOT NULL DEFAULT 'scholarship'
                         CHECK (notice_type IN ('scholarship', 'fellowship', 'grant')),
    title                TEXT NOT NULL,
    provider             TEXT,
    education_level      TEXT,
    discipline           TEXT,
    region               TEXT,
    residency_rule       TEXT,
    funding_category     TEXT,
    benefit_text         TEXT,
    eligibility_excerpt  TEXT,
    eligibility_keywords TEXT,                    -- keywords found in text, NOT a decision
    due_at               TEXT,                    -- UTC 'YYYY-MM-DDTHH:MM:SSZ'; NULL = unknown
    due_raw              TEXT,                    -- deadline text exactly as published
    timezone             TEXT,                    -- IANA name or 'UTC+05:30'
    tz_assumed           INTEGER NOT NULL DEFAULT 0 CHECK (tz_assumed IN (0, 1)),
    time_assumed         INTEGER NOT NULL DEFAULT 0 CHECK (time_assumed IN (0, 1)),
    captured_at          TEXT NOT NULL,           -- when this content version was captured
    first_seen_at        TEXT NOT NULL,
    verified_at          TEXT NOT NULL,           -- last successful check that still matches
    content_hash         TEXT NOT NULL,
    notice_status        TEXT NOT NULL DEFAULT 'active'
                         CHECK (notice_status IN ('active', 'withdrawn', 'unlisted')),
    version              INTEGER NOT NULL DEFAULT 1,
    UNIQUE (source_id, external_key)
);
CREATE INDEX idx_notices_due ON notices (due_at);
CREATE INDEX idx_notices_status ON notices (notice_status);

CREATE TABLE changes (
    change_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id         TEXT NOT NULL REFERENCES notices (notice_id),
    version           INTEGER NOT NULL,
    change_type       TEXT NOT NULL CHECK (change_type IN (
                          'deadline_revised', 'deadline_set', 'deadline_removed',
                          'withdrawn', 'reinstated', 'details_updated')),
    previous_deadline TEXT,
    updated_deadline  TEXT,
    previous_local    TEXT,
    updated_local     TEXT,
    detected_at       TEXT NOT NULL,
    UNIQUE (notice_id, version)
);

CREATE TABLE alerts (
    alert_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id         INTEGER NOT NULL UNIQUE REFERENCES changes (change_id),
    notice_id         TEXT NOT NULL REFERENCES notices (notice_id),
    message           TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    delivery_status   TEXT NOT NULL DEFAULT 'pending'
                      CHECK (delivery_status IN ('pending', 'delivered', 'failed')),
    delivery_attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE delivery_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES alerts (alert_id),
    channel  TEXT NOT NULL,
    status   TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    detail   TEXT,
    at       TEXT NOT NULL
);

CREATE TABLE scrape_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL REFERENCES sources (source_id),
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running', 'ok', 'warning', 'failed')),
    http_status     INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    notices_found   INTEGER NOT NULL DEFAULT 0,
    notices_new     INTEGER NOT NULL DEFAULT 0,
    notices_changed INTEGER NOT NULL DEFAULT 0,
    warnings        TEXT,
    error           TEXT
);

CREATE TABLE app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO app_meta (key, value) VALUES ('data_version', '0');
