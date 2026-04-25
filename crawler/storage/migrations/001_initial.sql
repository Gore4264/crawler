-- Migration 001: initial schema (E1 scope).
-- Creates the schema_migrations bookkeeping table plus the four E1 tables
-- (mentions, signals, scan_log, usage_log) with their indexes.
--
-- Applied as a single transaction by crawler.storage.migrate.
-- Idempotency lives in the runner (it skips already-applied versions),
-- not in this file: re-running this SQL standalone would error on the
-- already-existing tables.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER     PRIMARY KEY,
    filename   TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum   TEXT        NOT NULL
);

CREATE TABLE mentions (
    id                       UUID         PRIMARY KEY,
    content_hash             CHAR(64)     NOT NULL UNIQUE,
    source_id                TEXT         NOT NULL,
    external_id              TEXT         NOT NULL,
    author                   TEXT,
    author_id                TEXT,
    text                     TEXT         NOT NULL,
    text_html                TEXT,
    url                      TEXT         NOT NULL,
    lang_hint                TEXT,
    engagement               JSONB        NOT NULL DEFAULT '{}'::jsonb,
    raw                      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    published_at             TIMESTAMPTZ  NOT NULL,
    discovered_at            TIMESTAMPTZ  NOT NULL,
    fetched_at               TIMESTAMPTZ  NOT NULL,
    text_clean               TEXT         NOT NULL,
    lang                     TEXT         NOT NULL,
    is_html_stripped         BOOLEAN      NOT NULL,
    normalize_version        INTEGER      NOT NULL DEFAULT 1,
    tracking_params_removed  TEXT[]       NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mentions_source_discovered
    ON mentions(source_id, discovered_at DESC);

CREATE TABLE signals (
    id                UUID          PRIMARY KEY,
    mention_id        UUID          NOT NULL REFERENCES mentions(id) ON DELETE RESTRICT,
    project_id        TEXT          NOT NULL,
    matched_query     TEXT          NOT NULL,
    relevance_score   REAL          NOT NULL CHECK (relevance_score >= 0.0 AND relevance_score <= 1.0),
    is_spam           BOOLEAN       NOT NULL,
    intent            TEXT          NOT NULL CHECK (intent IN (
                          'complaint','question','recommendation',
                          'advertisement','news','discussion','other')),
    sentiment         TEXT          NOT NULL CHECK (sentiment IN ('positive','neutral','negative')),
    entities          JSONB         NOT NULL DEFAULT '[]'::jsonb,
    topics            JSONB         NOT NULL DEFAULT '[]'::jsonb,
    pipeline_trace    JSONB         NOT NULL,
    cost_usd          NUMERIC(12,6) NOT NULL DEFAULT 0,
    signal_created_at TIMESTAMPTZ   NOT NULL,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signals_project_signal_created
    ON signals(project_id, signal_created_at DESC);
CREATE INDEX idx_signals_project_intent
    ON signals(project_id, intent);
CREATE INDEX idx_signals_mention
    ON signals(mention_id);

CREATE TABLE scan_log (
    scan_id      UUID          PRIMARY KEY,
    project_id   TEXT          NOT NULL,
    source_id    TEXT          NOT NULL,
    query_name   TEXT          NOT NULL,
    started_at   TIMESTAMPTZ   NOT NULL,
    finished_at  TIMESTAMPTZ   NOT NULL,
    count        INTEGER       NOT NULL,
    cost_usd     NUMERIC(12,6) NOT NULL,
    status       TEXT          NOT NULL CHECK (status IN ('ok','partial','failed')),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_scan_log_last_scanned
    ON scan_log(project_id, source_id, query_name, finished_at DESC)
    WHERE status IN ('ok','partial');

CREATE TABLE usage_log (
    id           UUID          PRIMARY KEY,
    project_id   TEXT          NOT NULL,
    source_id    TEXT          NOT NULL,
    cost_usd     NUMERIC(12,6) NOT NULL,
    occurred_at  TIMESTAMPTZ   NOT NULL,
    kind         TEXT          NOT NULL CHECK (kind IN ('source','embedding','llm','other')),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_usage_log_project_occurred
    ON usage_log(project_id, occurred_at DESC);

CREATE INDEX idx_usage_log_project_source_occurred
    ON usage_log(project_id, source_id, occurred_at DESC);
