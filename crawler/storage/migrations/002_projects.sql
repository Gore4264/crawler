-- Migration 002: projects table + FK constraints on signals/scan_log/usage_log
-- Applied by storage/migrate.py runner (sha256-checksum idempotency).

CREATE TABLE projects (
    id            TEXT         PRIMARY KEY,
    name          TEXT         NOT NULL,
    config        JSONB        NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    CONSTRAINT projects_id_slug CHECK (id ~ '^[a-z0-9_-]+$')
);

CREATE INDEX idx_projects_active_created ON projects(is_active, created_at DESC);

-- FK: signals.project_id → projects.id  ON DELETE CASCADE
ALTER TABLE signals
    ADD CONSTRAINT fk_signals_project_id
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

-- FK: scan_log.project_id → projects.id  ON DELETE CASCADE
ALTER TABLE scan_log
    ADD CONSTRAINT fk_scan_log_project_id
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

-- FK: usage_log.project_id → projects.id  ON DELETE CASCADE
ALTER TABLE usage_log
    ADD CONSTRAINT fk_usage_log_project_id
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;
