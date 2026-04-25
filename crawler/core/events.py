"""DomainEvent base + 12 concrete events. Section C of core/CLAUDE.md.

`event_type` is a `ClassVar[str]` so it does not serialise into payload —
the bus wraps published events in a JSON envelope keyed on event_type.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    BudgetScope,
    FeedbackKind,
    Intent,
    ScanStatus,
    SourceQuery,
)


# --- C.1. Base ---------------------------------------------------------------


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    project_id: str | None = None

    event_type: ClassVar[str] = "domain.event"


# --- C.2. Concrete events ----------------------------------------------------


class ScanRequested(DomainEvent):
    event_type: ClassVar[str] = "scan.requested"
    scan_id: UUID
    query_name: str
    source_id: str
    requested_query: SourceQuery


class ScanStarted(DomainEvent):
    event_type: ClassVar[str] = "scan.started"
    scan_id: UUID
    source_id: str
    query_name: str
    started_at: datetime


class MentionsFetched(DomainEvent):
    """Emitted on every batch from a source — splits long scans."""

    event_type: ClassVar[str] = "mentions.fetched"
    scan_id: UUID
    batch_id: UUID
    count: int
    finished_at: datetime


class ScanFinished(DomainEvent):
    event_type: ClassVar[str] = "scan.finished"
    scan_id: UUID
    source_id: str
    query_name: str
    total_count: int
    cost_usd: Decimal
    status: ScanStatus


class ScanFailed(DomainEvent):
    event_type: ClassVar[str] = "scan.failed"
    scan_id: UUID
    source_id: str
    query_name: str
    error: str
    error_class: str


class MentionNormalized(DomainEvent):
    event_type: ClassVar[str] = "mention.normalized"
    mention_id: UUID
    content_hash: str


class MentionDeduped(DomainEvent):
    """Observability: why a mention was dropped on DedupStage."""

    event_type: ClassVar[str] = "mention.deduped"
    content_hash: str
    source_id: str
    reason: Literal["exact_hash", "minhash"]


class SignalReady(DomainEvent):
    event_type: ClassVar[str] = "signal.ready"
    signal_id: UUID
    mention_id: UUID
    matched_query: str
    relevance_score: float
    intent: Intent


class BudgetWarning(DomainEvent):
    event_type: ClassVar[str] = "budget.warning"
    current_usd: Decimal
    threshold_usd: Decimal
    fraction: float
    scope: BudgetScope
    source_id: str | None = None


class BudgetExhausted(DomainEvent):
    event_type: ClassVar[str] = "budget.exhausted"
    current_usd: Decimal
    limit_usd: Decimal
    scope: BudgetScope
    source_id: str | None = None


class SourceHealthChanged(DomainEvent):
    """Global event — `project_id` is always None."""

    event_type: ClassVar[str] = "source.health_changed"
    source_id: str
    healthy: bool
    error: str | None = None


class FeedbackReceived(DomainEvent):
    event_type: ClassVar[str] = "feedback.received"
    signal_id: UUID
    kind: FeedbackKind
    target: dict[str, Any] | None = None
    received_at: datetime
