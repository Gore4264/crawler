"""Signals service — search, retrieve, usage summary.

Functions used by `crawler signals`, `crawler signal show`, `crawler usage` commands.
"""
from __future__ import annotations

import dataclasses
import datetime
from decimal import Decimal
from uuid import UUID

from crawler.core.contracts import IRepository
from crawler.core.models import Intent, NormalizedMention, Signal


async def search_signals(
    repo: IRepository,
    project_id: str,
    *,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    intent: Intent | None = None,
    min_score: float | None = None,
    limit: int = 50,
    text_query: str | None = None,
) -> list[Signal]:
    """
    Return signals for a project.

    If text_query is set — ILIKE filter on text_clean via JOIN (E1, pre-E2).
    Otherwise plain feed ordered by signal_created_at DESC.
    """
    return await repo.search_signals(
        project_id,
        since=since,
        until=until,
        intent=intent,
        min_score=min_score,
        limit=limit,
        query=text_query,
    )


async def get_signal_with_mention(
    repo: IRepository,
    signal_id: UUID,
) -> tuple[Signal, NormalizedMention] | None:
    """
    Return (Signal, NormalizedMention) pair or None if signal not found.

    Uses new IRepository.get_mention() method.
    """
    signal = await repo.get_signal(signal_id)
    if signal is None:
        return None
    mention = await repo.get_mention(signal.mention_id)
    if mention is None:
        return None
    return signal, mention


async def count_signals(
    repo: IRepository,
    project_id: str,
    *,
    since: datetime.datetime | None = None,
) -> int:
    """Return count of signals for project (optional time filter)."""
    return await repo.count_signals(project_id, since=since)


@dataclasses.dataclass
class UsageSummary:
    project_id: str
    period_start: datetime.datetime
    total_usd: Decimal
    by_kind: dict[str, Decimal]
    by_source: dict[str, Decimal]
    signals_count: int
    cost_per_signal: Decimal | None


async def get_usage_summary(
    repo: IRepository,
    project_id: str,
    *,
    since: datetime.datetime,
) -> UsageSummary:
    """
    Aggregate usage_log for project since date.

    SELECT kind, source_id, SUM(cost_usd) FROM usage_log
    WHERE project_id=... AND occurred_at>=...
    GROUP BY kind, source_id.
    """
    rows = await repo.get_usage_by_period(project_id, since=since)

    by_kind: dict[str, Decimal] = {}
    by_source: dict[str, Decimal] = {}
    total = Decimal("0")

    for row in rows:
        kind = row["kind"]
        source = row["source_id"]
        amount = Decimal(str(row["total"]))
        by_kind[kind] = by_kind.get(kind, Decimal("0")) + amount
        by_source[source] = by_source.get(source, Decimal("0")) + amount
        total += amount

    signals_count = await repo.count_signals(project_id, since=since)
    cost_per_signal = (
        total / signals_count if signals_count > 0 else None
    )

    return UsageSummary(
        project_id=project_id,
        period_start=since,
        total_usd=total,
        by_kind=by_kind,
        by_source=by_source,
        signals_count=signals_count,
        cost_per_signal=cost_per_signal,
    )
