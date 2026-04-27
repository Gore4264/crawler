"""Repository — single asyncpg-backed implementation of IRepository.

Scope: E1 methods are fully implemented. Out-of-scope methods raise
NotImplementedError with a pointer to the future stage (see C.5 in
storage/CLAUDE.md). One class so DI is `repo: IRepository` everywhere;
mixin-split is reserved for Phase 1+ if the file outgrows ~1500 lines.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from ..core.contracts import IRepository
from ..core.models import (
    FeedbackKind,
    Intent,
    NormalizedMention,
    NotificationChannel,
    NotificationStatus,
    Project,
    ScanStatus,
    Signal,
    UsageKind,
)
from .database import Database

# Columns selected for read-side reconstruction of Signal.
# `signal_created_at AS created_at` aliases the column back to the
# pydantic field name (storage/CLAUDE.md A.2).
_SIGNAL_SELECT_COLUMNS = (
    "id, mention_id, project_id, matched_query, "
    "relevance_score, is_spam, intent, sentiment, "
    "entities, topics, pipeline_trace, cost_usd, "
    "signal_created_at AS created_at"
)


def _signal_from_row(row: Any) -> Signal:
    """asyncpg.Record → Signal. JSONB columns arrive as Python lists/dicts
    (the codec set in Database._init_connection runs json.loads)."""
    return Signal.model_validate(dict(row))


class Repository(IRepository):
    """Single point of entry to Postgres for all consumers."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ----- Mentions ---------------------------------------------------------

    async def bulk_upsert_mentions_with_dedup(
        self, mentions: list[NormalizedMention]
    ) -> tuple[int, int]:
        """INSERT ... ON CONFLICT (content_hash) DO NOTHING via UNNEST.

        Returns (inserted, skipped). asyncpg.executemany cannot fetch
        RETURNING rows, so we use a single statement that unpacks parallel
        arrays per column — this returns inserted ids and lets us count
        skips as `len(input) - inserted`."""
        if not mentions:
            return (0, 0)

        ids: list[UUID] = []
        content_hashes: list[str] = []
        source_ids: list[str] = []
        external_ids: list[str] = []
        authors: list[str | None] = []
        author_ids: list[str | None] = []
        texts: list[str] = []
        text_htmls: list[str | None] = []
        urls: list[str] = []
        lang_hints: list[str | None] = []
        engagements: list[str] = []
        raws: list[str] = []
        published_ats: list[datetime] = []
        discovered_ats: list[datetime] = []
        fetched_ats: list[datetime] = []
        text_cleans: list[str] = []
        langs: list[str] = []
        is_html_strippeds: list[bool] = []
        normalize_versions: list[int] = []
        tracking_paramss: list[str] = []

        for m in mentions:
            ids.append(m.id)
            content_hashes.append(m.content_hash)
            source_ids.append(m.source_id)
            external_ids.append(m.external_id)
            authors.append(m.author)
            author_ids.append(m.author_id)
            texts.append(m.text)
            text_htmls.append(m.text_html)
            urls.append(str(m.url))
            lang_hints.append(m.lang_hint)
            engagements.append(json.dumps(m.engagement))
            raws.append(json.dumps(m.raw))
            published_ats.append(m.published_at)
            discovered_ats.append(m.discovered_at)
            fetched_ats.append(m.fetched_at)
            text_cleans.append(m.text_clean)
            langs.append(m.lang)
            is_html_strippeds.append(m.is_html_stripped)
            normalize_versions.append(m.normalize_version)
            tracking_paramss.append(json.dumps(list(m.tracking_params_removed)))

        # `tracking_params_removed` is a per-row text[] of variable length,
        # so it cannot ride in a flat text[][] alongside the other UNNEST
        # columns (UNNEST collapses the outer dimension). Carry it as
        # jsonb[] and rebuild the text[] inside SELECT via
        # jsonb_array_elements_text. The remaining columns map 1:1.
        sql = """
        INSERT INTO mentions (
            id, content_hash, source_id, external_id,
            author, author_id, text, text_html, url, lang_hint,
            engagement, raw, published_at, discovered_at, fetched_at,
            text_clean, lang, is_html_stripped, normalize_version,
            tracking_params_removed
        )
        SELECT
            u.id, u.content_hash, u.source_id, u.external_id,
            u.author, u.author_id, u.text, u.text_html, u.url, u.lang_hint,
            u.engagement, u.raw,
            u.published_at, u.discovered_at, u.fetched_at,
            u.text_clean, u.lang, u.is_html_stripped, u.normalize_version,
            COALESCE(
                ARRAY(
                    SELECT jsonb_array_elements_text(u.tracking_params::jsonb)
                ),
                ARRAY[]::text[]
            )
        FROM UNNEST(
            $1::uuid[],
            $2::char(64)[],
            $3::text[],
            $4::text[],
            $5::text[],
            $6::text[],
            $7::text[],
            $8::text[],
            $9::text[],
            $10::text[],
            $11::jsonb[],
            $12::jsonb[],
            $13::timestamptz[],
            $14::timestamptz[],
            $15::timestamptz[],
            $16::text[],
            $17::text[],
            $18::boolean[],
            $19::integer[],
            $20::text[]
        ) AS u(
            id, content_hash, source_id, external_id,
            author, author_id, text, text_html, url, lang_hint,
            engagement, raw, published_at, discovered_at, fetched_at,
            text_clean, lang, is_html_stripped, normalize_version,
            tracking_params
        )
        ON CONFLICT (content_hash) DO NOTHING
        RETURNING id
        """
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                sql,
                ids,
                content_hashes,
                source_ids,
                external_ids,
                authors,
                author_ids,
                texts,
                text_htmls,
                urls,
                lang_hints,
                engagements,
                raws,
                published_ats,
                discovered_ats,
                fetched_ats,
                text_cleans,
                langs,
                is_html_strippeds,
                normalize_versions,
                tracking_paramss,
            )
        inserted = len(rows)
        skipped = len(mentions) - inserted
        return (inserted, skipped)

    async def existing_hashes(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT content_hash FROM mentions "
                "WHERE content_hash = ANY($1::char(64)[])",
                hashes,
            )
        return {row["content_hash"] for row in rows}

    # ----- Signals ----------------------------------------------------------

    async def insert_signals(self, signals: list[Signal]) -> int:
        if not signals:
            return 0
        rows = [
            (
                s.id,
                s.mention_id,
                s.project_id,
                s.matched_query,
                s.relevance_score,
                s.is_spam,
                s.intent,
                s.sentiment,
                json.dumps(list(s.entities)),
                json.dumps(list(s.topics)),
                json.dumps(
                    [t.model_dump(mode="json") for t in s.pipeline_trace]
                ),
                s.cost_usd,
                s.created_at,
            )
            for s in signals
        ]
        async with self._db.transaction() as conn:
            await conn.executemany(
                "INSERT INTO signals "
                "(id, mention_id, project_id, matched_query, relevance_score, "
                "is_spam, intent, sentiment, entities, topics, pipeline_trace, "
                "cost_usd, signal_created_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,"
                "$11::jsonb,$12,$13)",
                rows,
            )
        return len(signals)

    async def get_signal(self, signal_id: UUID) -> Signal | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_SIGNAL_SELECT_COLUMNS} FROM signals WHERE id = $1",
                signal_id,
            )
        if row is None:
            return None
        return _signal_from_row(row)

    async def search_signals(
        self,
        project_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        intent: Intent | None = None,
        min_score: float | None = None,
        limit: int = 100,
    ) -> list[Signal]:
        sql = f"""
        SELECT {_SIGNAL_SELECT_COLUMNS}
        FROM signals
        WHERE project_id = $1
          AND ($2::timestamptz IS NULL OR signal_created_at >= $2)
          AND ($3::timestamptz IS NULL OR signal_created_at <= $3)
          AND ($4::text IS NULL OR intent = $4)
          AND ($5::real IS NULL OR relevance_score >= $5)
        ORDER BY signal_created_at DESC
        LIMIT $6
        """
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                sql,
                project_id,
                since,
                until,
                intent,
                min_score,
                limit,
            )
        return [_signal_from_row(r) for r in rows]

    async def search_hybrid(
        self,
        project_id: str,
        text: str,
        query_vector: list[float],
        k: int = 50,
    ) -> list[Signal]:
        raise NotImplementedError("requires storage support from E2a")

    # ----- Scan log ---------------------------------------------------------

    async def last_scanned_at(
        self, project_id: str, source_id: str, query_name: str
    ) -> datetime | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT MAX(finished_at) AS last "
                "FROM scan_log "
                "WHERE project_id = $1 "
                "  AND source_id = $2 "
                "  AND query_name = $3 "
                "  AND status IN ('ok','partial')",
                project_id,
                source_id,
                query_name,
            )
        return None if row is None else row["last"]

    async def record_scan(
        self,
        scan_id: UUID,
        project_id: str,
        source_id: str,
        query_name: str,
        started_at: datetime,
        finished_at: datetime,
        count: int,
        cost_usd: Decimal,
        status: ScanStatus,
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "INSERT INTO scan_log "
                "(scan_id, project_id, source_id, query_name, "
                "started_at, finished_at, count, cost_usd, status) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                scan_id,
                project_id,
                source_id,
                query_name,
                started_at,
                finished_at,
                count,
                cost_usd,
                status,
            )

    # ----- Usage / budget ---------------------------------------------------

    async def append_usage(
        self,
        project_id: str,
        source_id: str,
        cost_usd: Decimal,
        occurred_at: datetime,
        kind: UsageKind,
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "INSERT INTO usage_log "
                "(id, project_id, source_id, cost_usd, occurred_at, kind) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                uuid4(),
                project_id,
                source_id,
                cost_usd,
                occurred_at,
                kind,
            )

    async def budget_used(
        self,
        project_id: str,
        since: datetime,
        until: datetime | None = None,
    ) -> Decimal:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total "
                "FROM usage_log "
                "WHERE project_id = $1 "
                "  AND occurred_at >= $2 "
                "  AND ($3::timestamptz IS NULL OR occurred_at <= $3)",
                project_id,
                since,
                until,
            )
        assert row is not None
        return Decimal(row["total"])

    async def budget_used_by_source(
        self, project_id: str, source_id: str, since: datetime
    ) -> Decimal:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total "
                "FROM usage_log "
                "WHERE project_id = $1 "
                "  AND source_id = $2 "
                "  AND occurred_at >= $3",
                project_id,
                source_id,
                since,
            )
        assert row is not None
        return Decimal(row["total"])

    # ----- Notifications (E5 stub) ------------------------------------------

    async def notification_already_sent(
        self,
        project_id: str,
        signal_id: UUID,
        channel: NotificationChannel,
        target: str,
    ) -> bool:
        raise NotImplementedError("requires storage support from E5")

    async def record_notification(
        self,
        project_id: str,
        signal_id: UUID,
        channel: NotificationChannel,
        target: str,
        sent_at: datetime,
        status: NotificationStatus,
    ) -> None:
        raise NotImplementedError("requires storage support from E5")

    # ----- Projects (E2c stub) ---------------------------------------------

    async def upsert_project(self, project: Project, yaml_source: str) -> None:
        raise NotImplementedError("requires storage support from E2c")

    async def get_project(self, id: str) -> Project | None:
        raise NotImplementedError("requires storage support from E2c")

    async def list_projects(self) -> list[Project]:
        raise NotImplementedError("requires storage support from E2c")

    # ----- Feedback (E5 stub) -----------------------------------------------

    async def record_feedback(
        self,
        signal_id: UUID,
        kind: FeedbackKind,
        created_at: datetime,
        target: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError("requires storage support from E5")
