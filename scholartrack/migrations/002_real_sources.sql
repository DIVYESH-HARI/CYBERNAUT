-- foreign_keys: off
-- v2: adds the 'detail_pages' and 'blocks' adapters, per-source refresh interval, demo flag,
-- and notices.year_inferred. SQLite cannot change a CHECK constraint in place, so the sources
-- table is rebuilt (the standard SQLite 12-step procedure; app/db.py turns foreign keys off for it).

CREATE TABLE sources_new (
    source_id            TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    listing_url          TEXT NOT NULL,
    adapter              TEXT NOT NULL CHECK (adapter IN ('table', 'cards', 'detail_pages', 'blocks')),
    default_timezone     TEXT NOT NULL DEFAULT 'UTC',
    enabled              INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    is_demo              INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
    refresh_minutes      INTEGER NOT NULL DEFAULT 60 CHECK (refresh_minutes >= 5),
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

INSERT INTO sources_new (source_id, name, listing_url, adapter, default_timezone, enabled, is_demo, approved_by,
                         approved_at, last_checked_at, last_success_at, last_status, last_error,
                         consecutive_failures, config_json)
SELECT source_id, name, listing_url, adapter, default_timezone, enabled,
       CASE WHEN source_id LIKE 'demo-%' THEN 1 ELSE 0 END, approved_by, approved_at, last_checked_at,
       last_success_at, last_status, last_error, consecutive_failures, config_json
FROM sources;

DROP TABLE sources;
ALTER TABLE sources_new RENAME TO sources;

ALTER TABLE notices ADD COLUMN year_inferred INTEGER NOT NULL DEFAULT 0 CHECK (year_inferred IN (0, 1));
