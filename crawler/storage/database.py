"""Database — async connection pool over asyncpg.

Owns lifecycle (connect/disconnect), per-connection JSONB codec, and the
init-extensions check that fails fast when pgvector or pgmq are missing
(Inv 7 in storage/CLAUDE.md).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

_JSONB_VERSION = b"\x01"


def _jsonb_encode(value: Any) -> bytes:
    """Encoder for the binary jsonb codec.

    Accepts either a Python value (dict/list/scalar) — json.dumps'd here —
    or a pre-serialized JSON string (used by bulk-insert paths that
    serialize once and pass the same string to multiple parameters)."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, bytes):
        text = value.decode("utf-8")
    else:
        text = json.dumps(value)
    return _JSONB_VERSION + text.encode("utf-8")


def _jsonb_decode(buf: bytes) -> Any:
    """Decoder for the binary jsonb codec — strips the leading version
    byte and json.loads the rest."""
    return json.loads(bytes(buf)[1:])


class Database:
    """Thin wrapper around asyncpg.Pool.

    Use:
        db = Database(dsn)
        await db.connect()
        async with db.acquire() as conn:
            ...
        await db.disconnect()
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._pool

    async def connect(self) -> None:
        """Create pool, register per-connection codecs, ensure required
        extensions are loadable. Raises if pgvector or pgmq cannot be
        created in the target database."""
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            init=self._init_connection,
        )
        await self._init_extensions()

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        """Per-connection setup. asyncpg maps UUID, Decimal, TIMESTAMPTZ
        natively. JSONB needs a binary codec — the text codec only fires
        for literal `'...'::jsonb` values, not for column reads, because
        asyncpg requests jsonb columns in binary format. Postgres binary
        jsonb starts with a one-byte version (0x01) followed by the JSON
        text payload, hence the leading byte handling below."""
        await conn.set_type_codec(
            "jsonb",
            encoder=_jsonb_encode,
            decoder=_jsonb_decode,
            schema="pg_catalog",
            format="binary",
        )

    async def _init_extensions(self) -> None:
        """Load extensions required for the full Phase 0 footprint.

        pgvector — used by E2a (separate `mentions_embeddings` table).
        pgmq    — used by E4 (queue for orchestration).

        Both are loaded eagerly here so a misconfigured Postgres image is
        detected on startup rather than on first feature use. CREATE
        EXTENSION IF NOT EXISTS is cheap on subsequent calls.
        """
        async with self.acquire() as conn:
            # pgvector ships under the extension name `vector`, not
            # `pgvector` — see https://github.com/pgvector/pgvector.
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgmq")

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Yields an asyncpg pool connection (PoolConnectionProxy). Typed as
        Any to keep callers free of asyncpg's narrower internal types."""
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """Yields a pool connection inside a transaction."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn
