-- TransitNotice schema v1. All timestamps are UTC ISO-8601 strings: YYYY-MM-DDTHH:MM:SSZ
-- (fixed width, so lexicographic order == chronological order).

CREATE TABLE sources (
    source_id            INTEGER PRIMARY KEY,
    source_key           TEXT NOT NULL UNIQUE,
    agency               TEXT NOT NULL,
    base_url             TEXT NOT NULL,
    timezone             TEXT NOT NULL,
    refresh_seconds      INTEGER NOT NULL DEFAULT 900,
    enabled              INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    last_attempt_at      TEXT,
    last_success_at      TEXT,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_run_at          TEXT
);

CREATE TABLE notices (
    notice_id         TEXT PRIMARY KEY,                 -- "<source_key>:<external_id>"
    source_id         INTEGER NOT NULL REFERENCES sources(source_id),
    external_id       TEXT NOT NULL,
    agency            TEXT NOT NULL,
    route_id          TEXT NOT NULL DEFAULT '',         -- comma separated, see notice_routes
    area              TEXT NOT NULL DEFAULT '',
    source_url        TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN
                        ('detour','suspension','delay','station_closure','schedule_change','construction','accessibility','other')),
    summary           TEXT NOT NULL,
    published_at      TEXT,
    valid_from        TEXT,
    valid_to          TEXT,                             -- NULL = open ended
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','withdrawn','cancelled','expired')),
    observed_at       TEXT NOT NULL,                    -- first time we saw it
    last_confirmed_at TEXT NOT NULL,                    -- last successful fetch that still listed it
    content_hash      TEXT NOT NULL,
    revision_id       INTEGER NOT NULL DEFAULT 1,
    change_summary    TEXT NOT NULL DEFAULT 'First observed',
    alert_status      TEXT NOT NULL DEFAULT 'none' CHECK (alert_status IN ('none','pending','logged','sent','failed')),
    alerted_at        TEXT,
    UNIQUE (source_id, external_id),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);
CREATE INDEX idx_notices_window ON notices (valid_from, valid_to);
CREATE INDEX idx_notices_status ON notices (status);

CREATE TABLE notice_routes (
    notice_id TEXT NOT NULL REFERENCES notices(notice_id) ON DELETE CASCADE,
    route_id  TEXT NOT NULL,
    PRIMARY KEY (notice_id, route_id)
);
CREATE INDEX idx_notice_routes_route ON notice_routes (route_id);

CREATE TABLE notice_stations (
    notice_id TEXT NOT NULL REFERENCES notices(notice_id) ON DELETE CASCADE,
    station   TEXT NOT NULL,
    PRIMARY KEY (notice_id, station)
);
CREATE INDEX idx_notice_stations_station ON notice_stations (station);

CREATE TABLE notice_revisions (
    notice_id      TEXT NOT NULL REFERENCES notices(notice_id) ON DELETE CASCADE,
    revision_id    INTEGER NOT NULL,
    content_hash   TEXT NOT NULL,
    type           TEXT NOT NULL,
    summary        TEXT NOT NULL,
    valid_from     TEXT,
    valid_to       TEXT,
    status         TEXT NOT NULL,
    route_id       TEXT NOT NULL,
    area           TEXT NOT NULL,
    observed_at    TEXT NOT NULL,
    change_summary TEXT NOT NULL,
    PRIMARY KEY (notice_id, revision_id)
);

CREATE TABLE fetch_runs (
    run_id         INTEGER PRIMARY KEY,
    source_id      INTEGER NOT NULL REFERENCES sources(source_id),
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    outcome        TEXT NOT NULL DEFAULT 'running' CHECK (outcome IN ('running','ok','error')),
    http_status    INTEGER,
    items_parsed   INTEGER NOT NULL DEFAULT 0,
    items_skipped  INTEGER NOT NULL DEFAULT 0,
    items_new      INTEGER NOT NULL DEFAULT 0,
    items_revised  INTEGER NOT NULL DEFAULT 0,
    items_withdrawn INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);

CREATE TABLE subscribers (
    subscriber_id     INTEGER PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,
    subscriber_filter TEXT NOT NULL DEFAULT '{}',       -- JSON: {"routes":[],"areas":[],"types":[],"stations":[]}
    created_at        TEXT NOT NULL,
    active            INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE alert_log (
    alert_id      INTEGER PRIMARY KEY,
    notice_id     TEXT NOT NULL REFERENCES notices(notice_id) ON DELETE CASCADE,
    revision_id   INTEGER NOT NULL,
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(subscriber_id) ON DELETE CASCADE,
    alert_status  TEXT NOT NULL CHECK (alert_status IN ('pending','logged','sent','failed')),
    alerted_at    TEXT,
    detail        TEXT,
    UNIQUE (notice_id, revision_id, subscriber_id)      -- one alert per new/revised advisory per subscriber
);
