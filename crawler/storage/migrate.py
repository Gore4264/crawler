"""Plain-SQL migration runner.

Discovers `NNN_*.sql` files in `crawler/storage/migrations/`, applies
un-applied versions in order, records each in `schema_migrations` with a
sha256 checksum, and aborts when a previously-applied file's checksum no
longer matches the file on disk (silent-drift defence).

CLI:
    python -m crawler.storage.migrate
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .database import Database

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
_BOOTSTRAP_SQL = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER     PRIMARY KEY,
    filename   TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum   TEXT        NOT NULL
);
"""


@dataclass(frozen=True)
class _Migration:
    version: int
    filename: str
    path: Path
    sql: str
    checksum: str


def _discover(migrations_dir: Path) -> list[_Migration]:
    found: list[_Migration] = []
    for path in sorted(migrations_dir.iterdir()):
        if path.suffix != ".sql":
            continue
        m = _FILENAME_RE.match(path.name)
        if not m:
            raise RuntimeError(
                f"Migration filename {path.name!r} does not match "
                f"NNN_short_description.sql"
            )
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        found.append(
            _Migration(
                version=int(m.group(1)),
                filename=path.name,
                path=path,
                sql=sql,
                checksum=checksum,
            )
        )
    versions = [m.version for m in found]
    if len(versions) != len(set(versions)):
        raise RuntimeError(f"Duplicate migration versions in {migrations_dir}")
    return found


async def run_migrations(
    database: Database, migrations_dir: Path = MIGRATIONS_DIR
) -> list[int]:
    """Apply all un-applied migrations from `migrations_dir`. Returns the
    list of versions newly applied (empty if nothing to do)."""
    migrations = _discover(migrations_dir)

    async with database.acquire() as conn:
        await conn.execute(_BOOTSTRAP_SQL)
        applied_rows = await conn.fetch(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )
    applied = {row["version"]: row["checksum"] for row in applied_rows}

    # Drift check: every previously-applied migration must match its file.
    for m in migrations:
        if m.version in applied and applied[m.version] != m.checksum:
            raise RuntimeError(
                f"Checksum mismatch for migration {m.filename}: "
                f"recorded={applied[m.version]} disk={m.checksum}. "
                f"An applied migration file was modified — write a new "
                f"compensating migration instead."
            )

    newly_applied: list[int] = []
    for m in migrations:
        if m.version in applied:
            continue
        async with database.transaction() as conn:
            await conn.execute(m.sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, filename, checksum) "
                "VALUES ($1, $2, $3)",
                m.version,
                m.filename,
                m.checksum,
            )
        newly_applied.append(m.version)
    return newly_applied


async def _main() -> int:
    dsn = os.environ.get("CRAWLER_DATABASE_DSN")
    if not dsn:
        print(
            "ERROR: CRAWLER_DATABASE_DSN is not set. See .env.example.",
            file=sys.stderr,
        )
        return 2
    db = Database(dsn)
    await db.connect()
    try:
        applied = await run_migrations(db)
    finally:
        await db.disconnect()
    if applied:
        print(f"Applied migrations: {applied}")
    else:
        print("No migrations to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
