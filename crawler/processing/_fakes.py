"""FakeRepository — in-memory IRepository for slice tests and CLI debugging.

Section C.1 of processing/CLAUDE.md.

Lives in processing/ (not tests/) so it's importable from any code before
full storage integration is complete (E1 integration session).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import UUID

from crawler.core.models import (
    FeedbackKind,
    Intent,
    NormalizedMention,
    NotificationChannel,
    NotificationStatus,
    ScanStatus,
    Signal,
    UsageKind,
)


class FakeRepository:
    """
    In-memory IRepository implementation for slice tests and pre-integration
    CLI debugging.

    Implements only E1-scope methods fully. All other methods raise
    NotImplementedError with a descriptive message pointing to the future
    epic that will implement them.

    Also provides fake-only cursor API (F.2 decision):
      - get_cursor(project_id, source_id, query_name) -> str | None
      - set_cursor(project_id, source_id, query_name, cursor) -> None
    These are NOT part of IRepository contract; they exist only on FakeRepository
    until the integration session adds the real source_cursors table.
    """

    def __init__(self) -> None:
        self._hashes: set[str] = set()
        self._mentions: list[NormalizedMention] = []
        self._signals: list[Signal] = []
        # fake-only cursor storage: (project_id, source_id, query_name) → cursor
        self._cursors: dict[tuple[str, str, str], str] = {}

    # -----------------------------------------------------------------------
    # Mentions
    # -----------------------------------------------------------------------

    async def bulk_upsert_mentions_with_dedup(
        self, mentions: list[NormalizedMention]
    ) -> tuple[int, int]:
        """INSERT-like: add new mentions, skip duplicates by content_hash."""
        inserted, skipped = 0, 0
        for m in mentions:
            if m.content_hash not in self._hashes:
                self._hashes.add(m.content_hash)
                self._mentions.append(m)
                inserted += 1
            else:
                skipped += 1
        return inserted, skipped

    async def existing_hashes(self, hashes: list[str]) -> set[str]:
        """Return subset of hashes already present in the fake store."""
        return self._hashes & set(hashes)

    # -----------------------------------------------------------------------
    # Signals
    # -----------------------------------------------------------------------

    async def insert_signals(self, signals: list[Signal]) -> int:
        self._signals.extend(signals)
        return len(signals)

    async def get_signal(self, signal_id: UUID) -> Signal | None:
        return next((s for s in self._signals if s.id == signal_id), None)

    async def search_signals(
        self,
        project_id: str,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
        intent: Intent | None = None,
        min_score: float | None = None,
        limit: int = 100,
    ) -> list[Signal]:
        results = [s for s in self._signals if s.project_id == project_id]
        if intent is not None:
            results = [s for s in results if s.intent == intent]
        if min_score is not None:
            results = [s for s in results if s.relevance_score >= min_score]
        return results[:limit]

    # -----------------------------------------------------------------------
    # Scan log
    # -----------------------------------------------------------------------

    async def last_scanned_at(
        self, project_id: str, source_id: str, query_name: str
    ) -> datetime.datetime | None:
        return None  # always "never scanned" in fake

    async def record_scan(
        self,
        scan_id: UUID,
        project_id: str,
        source_id: str,
        query_name: str,
        started_at: datetime.datetime,
        finished_at: datetime.datetime,
        count: int,
        cost_usd: Decimal,
        status: ScanStatus,
    ) -> None:
        pass  # no-op in fake

    # -----------------------------------------------------------------------
    # Usage / budget
    # -----------------------------------------------------------------------

    async def append_usage(
        self,
        project_id: str,
        source_id: str,
        cost_usd: Decimal,
        occurred_at: datetime.datetime,
        kind: UsageKind,
    ) -> None:
        pass  # no-op

    async def budget_used(
        self,
        project_id: str,
        since: datetime.datetime,
        until: datetime.datetime | None = None,
    ) -> Decimal:
        return Decimal("0")

    async def budget_used_by_source(
        self, project_id: str, source_id: str, since: datetime.datetime
    ) -> Decimal:
        return Decimal("0")

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------

    async def notification_already_sent(
        self,
        project_id: str,
        signal_id: UUID,
        channel: NotificationChannel,
        target: str,
    ) -> bool:
        raise NotImplementedError("requires E5")

    async def record_notification(
        self,
        project_id: str,
        signal_id: UUID,
        channel: NotificationChannel,
        target: str,
        sent_at: datetime.datetime,
        status: NotificationStatus,
    ) -> None:
        raise NotImplementedError("requires E5")

    # -----------------------------------------------------------------------
    # Projects
    # -----------------------------------------------------------------------

    async def upsert_project(self, project: object, yaml_source: str) -> None:
        raise NotImplementedError("requires E2c")

    async def get_project(self, id: str) -> object:  # Project | None
        raise NotImplementedError("requires E2c")

    async def list_projects(self) -> list[object]:
        raise NotImplementedError("requires E2c")

    # -----------------------------------------------------------------------
    # Feedback
    # -----------------------------------------------------------------------

    async def record_feedback(
        self,
        signal_id: UUID,
        kind: FeedbackKind,
        created_at: datetime.datetime,
        target: dict | None = None,  # type: ignore[type-arg]
    ) -> None:
        raise NotImplementedError("requires E5")

    # -----------------------------------------------------------------------
    # Hybrid search
    # -----------------------------------------------------------------------

    async def search_hybrid(
        self,
        project_id: str,
        text: str,
        query_vector: list[float],
        k: int = 50,
    ) -> list[Signal]:
        raise NotImplementedError("requires E2a")

    # -----------------------------------------------------------------------
    # Fake-only cursor API (F.2 decision from todo-002)
    # -----------------------------------------------------------------------

    def get_cursor(
        self, project_id: str, source_id: str, query_name: str
    ) -> str | None:
        """Return last known cursor for this (project, source, query) triple."""
        return self._cursors.get((project_id, source_id, query_name))

    def set_cursor(
        self, project_id: str, source_id: str, query_name: str, cursor: str
    ) -> None:
        """Persist cursor for this (project, source, query) triple."""
        self._cursors[(project_id, source_id, query_name)] = cursor
