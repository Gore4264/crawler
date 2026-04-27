"""Shared fixtures for the integration suite.

Tests assume Postgres is running and reachable via CRAWLER_DATABASE_DSN
(see .env.example). Bring it up with:

    docker compose up -d postgres

If the env var is missing, every integration test is skipped — local
runs without docker still produce a clean pass.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from crawler.storage import Database
from crawler.storage.migrate import run_migrations

# Tables we own — listed once so cleanup is explicit.
_OWNED_TABLES = ("signals", "scan_log", "usage_log", "mentions")


def _dsn() -> str | None:
    return os.environ.get("CRAWLER_DATABASE_DSN")


@pytest_asyncio.fixture(scope="session")
async def database() -> AsyncIterator[Database]:
    dsn = _dsn()
    if not dsn:
        pytest.skip(
            "CRAWLER_DATABASE_DSN is not set — integration tests skipped"
        )
    db = Database(dsn)
    await db.connect()
    await run_migrations(db)
    try:
        yield db
    finally:
        await db.disconnect()


@pytest_asyncio.fixture
async def db(database: Database) -> AsyncIterator[Database]:
    """Per-test fixture: truncates owned tables so tests are isolated.

    schema_migrations is left intact — re-running migrations between tests
    would be wasteful and is what we deliberately want to avoid.
    """
    async with database.acquire() as conn:
        # CASCADE handles the FK from signals → mentions, but we list both
        # tables in the right order anyway for readability.
        await conn.execute(
            f"TRUNCATE {', '.join(_OWNED_TABLES)} RESTART IDENTITY CASCADE"
        )
    yield database
