# Migrations

Plain SQL files. Applied in order by `crawler/storage/migrate.py`. No
Alembic, no SQLAlchemy.

## Naming

`NNN_short_description.sql`, three-digit zero-padded version, snake_case
description (≤5 words). Examples:

- `001_initial.sql` — first migration, all four E1 tables.
- `002_add_embeddings.sql` — E2a.
- `003_add_events_table.sql` — E4.

## Rules

- One migration = one file. Version is the leading `NNN`.
- Each file is applied as a single transaction. Statements requiring
  `CREATE INDEX CONCURRENTLY` (or other non-transactional DDL) must add
  the marker `-- NO_TRANSACTION` on the first line. Phase 0 has none.
- After a migration is applied its file MUST NOT change. The runner
  records sha256 in `schema_migrations.checksum` and aborts on mismatch.
  If you need to amend an applied migration, write a new compensating
  migration instead.
- DOWN migrations are not supported in Phase 0. Compensating forward
  migrations only.
- Keep `crawler/storage/schema.sql` in sync as the declarative snapshot.
