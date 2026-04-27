"""Protocol contracts and small data-models that live alongside them.

Section B of core/CLAUDE.md. All Protocol classes are static-only
(`runtime_checkable=False` — i.e. not decorated with @runtime_checkable).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
)
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    CostModel,
    FeedbackKind,
    Intent,
    NormalizedMention,
    NotificationChannel,
    NotificationConfig,
    NotificationStatus,
    Project,
    RawMention,
    ScanStatus,
    Sentiment,
    Signal,
    SourceQuery,
    UsageKind,
)

if TYPE_CHECKING:
    from .events import DomainEvent


# --- B.1. SourceCapabilities -------------------------------------------------


class SourceCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    supports_keywords: bool = True
    supports_semantic: bool = False
    supports_geo: bool = False
    supports_language_filter: bool = False
    supports_search: bool = True
    supports_streaming: bool = False
    supports_historical: bool = True
    cost_model: CostModel = "free"
    typical_latency_ms: int = 1000


# --- B.2. CostEstimate -------------------------------------------------------


class CostEstimate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_results: int
    expected_cost_usd: Decimal
    confidence: Literal["exact", "estimate", "unknown"]


# --- B.3. ISource ------------------------------------------------------------


class ISource(Protocol):
    id: str
    capabilities: SourceCapabilities

    async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]:
        """Async iterator yielding RawMention. Per spec (core/CLAUDE.md B.3)
        declared as `async def` — implementations are async generators
        (`async def` with `yield` body)."""
        ...

    async def health_check(self) -> bool: ...

    def estimate_cost(self, q: SourceQuery) -> CostEstimate: ...


# --- B.4. IStreamingSource ---------------------------------------------------


class IStreamingSource(ISource, Protocol):
    async def start(self) -> None:
        """Open connection and begin buffering incoming messages."""
        ...

    async def stop(self) -> None:
        """Close connection, drain buffer."""
        ...

    async def __aenter__(self) -> IStreamingSource: ...

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


# --- B.5. IEmbedder ----------------------------------------------------------


class IEmbedder(Protocol):
    model_id: str
    dimensions: int
    cost_per_1m_tokens: Decimal
    max_batch_size: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]: ...

    def estimate_cost(self, texts: list[str]) -> Decimal: ...


# --- B.6. IRepository --------------------------------------------------------


class IRepository(Protocol):
    # --- Mentions ---
    async def bulk_upsert_mentions_with_dedup(
        self, mentions: list[NormalizedMention]
    ) -> tuple[int, int]:
        """INSERT ... ON CONFLICT (content_hash) DO NOTHING. Returns
        (inserted, skipped)."""
        ...

    async def existing_hashes(self, hashes: list[str]) -> set[str]:
        """Subset of hashes already present. Used by DedupStage in bulk."""
        ...

    # --- Signals ---
    async def insert_signals(self, signals: list[Signal]) -> int: ...

    async def get_signal(self, signal_id: UUID) -> Signal | None: ...

    async def search_signals(
        self,
        project_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        intent: Intent | None = None,
        min_score: float | None = None,
        limit: int = 100,
        query: str | None = None,
    ) -> list[Signal]:
        """Read-only feed for API. query: ILIKE filter on text_clean (E1)."""
        ...

    async def search_hybrid(
        self,
        project_id: str,
        text: str,
        query_vector: list[float],
        k: int = 50,
    ) -> list[Signal]:
        """BM25 + cosine + RRF in one query (see ADR-0003)."""
        ...

    # --- Scan log ---
    async def last_scanned_at(
        self, project_id: str, source_id: str, query_name: str
    ) -> datetime | None: ...

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
    ) -> None: ...

    # --- Usage / budget ---
    async def append_usage(
        self,
        project_id: str,
        source_id: str,
        cost_usd: Decimal,
        occurred_at: datetime,
        kind: UsageKind,
    ) -> None: ...

    async def budget_used(
        self,
        project_id: str,
        since: datetime,
        until: datetime | None = None,
    ) -> Decimal: ...

    async def budget_used_by_source(
        self, project_id: str, source_id: str, since: datetime
    ) -> Decimal: ...

    # --- Notifications ---
    async def notification_already_sent(
        self,
        project_id: str,
        signal_id: UUID,
        channel: NotificationChannel,
        target: str,
    ) -> bool: ...

    async def record_notification(
        self,
        project_id: str,
        signal_id: UUID,
        channel: NotificationChannel,
        target: str,
        sent_at: datetime,
        status: NotificationStatus,
    ) -> None: ...

    # --- Projects ---
    async def create_project(self, project: Project) -> Project: ...

    async def list_projects(self, active_only: bool = True) -> list[Project]: ...

    async def get_project(self, project_id: str) -> Project | None: ...

    async def delete_project(self, project_id: str, *, cascade: bool = True) -> None: ...

    async def get_mention(self, mention_id: UUID) -> NormalizedMention | None: ...

    async def count_signals(
        self, project_id: str, since: datetime | None = None
    ) -> int: ...

    async def get_usage_by_period(
        self,
        project_id: str,
        since: datetime,
    ) -> list[dict]: ...

    # --- Feedback ---
    async def record_feedback(
        self,
        signal_id: UUID,
        kind: FeedbackKind,
        created_at: datetime,
        target: dict[str, Any] | None = None,
    ) -> None:
        """target reserved for D12; in Phase 0 always None."""
        ...


# --- B.7. IQueue -------------------------------------------------------------


class IQueue(Protocol):
    async def enqueue(
        self, queue: str, payload: dict[str, Any], *, delay_seconds: int = 0
    ) -> UUID:
        """Returns message_id."""
        ...

    async def dequeue(
        self, queue: str, *, visibility_timeout: int = 30
    ) -> tuple[UUID, dict[str, Any]] | None:
        """Returns (message_id, payload) or None if empty. Sets
        visibility_timeout — message reappears if not ack'd in time."""
        ...

    async def ack(self, queue: str, message_id: UUID) -> None: ...

    async def nack(
        self, queue: str, message_id: UUID, *, retry_after_seconds: int = 60
    ) -> None: ...

    async def peek_size(self, queue: str) -> int: ...


# --- B.8. IEventBus ----------------------------------------------------------


class Subscription(BaseModel):
    """Handle returned from `IEventBus.subscribe`. The actual `unsubscribe`
    behaviour lives in the `bus/` slice — Subscription is a frozen value
    object identifying the subscription."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    event_type: str


class IEventBus(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...

    async def subscribe(
        self,
        event_type: type[DomainEvent],
        handler: Callable[[DomainEvent], Awaitable[None]],
    ) -> Subscription: ...

    async def unsubscribe(self, subscription: Subscription) -> None: ...


# --- B.9. INotifier ----------------------------------------------------------


class NotificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: NotificationStatus
    external_id: str | None = None
    error: str | None = None
    cost_usd: Decimal = Decimal("0")


class INotifier(Protocol):
    channel: NotificationChannel

    async def send(
        self,
        signal: Signal,
        mention: NormalizedMention,
        config: NotificationConfig,
    ) -> NotificationResult: ...

    async def health_check(self) -> bool: ...


# --- B.10. IClassifier -------------------------------------------------------


class ClassificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: Intent
    sentiment: Sentiment
    entities: list[str]
    topics: list[str]
    is_spam: bool
    relevance_score: float = Field(ge=0.0, le=1.0)
    cost_usd: Decimal
    model_id: str
    latency_ms: int


class IClassifier(Protocol):
    model_id: str
    cost_per_1m_input_tokens: Decimal
    cost_per_1m_output_tokens: Decimal

    async def classify(
        self, mentions: list[NormalizedMention], project: Project
    ) -> list[ClassificationResult]: ...

    def estimate_cost(self, mentions: list[NormalizedMention]) -> Decimal: ...


# --- B.11. IStage ------------------------------------------------------------


class IStage(Protocol):
    name: str

    async def process(
        self,
        mentions: list[NormalizedMention],
        ctx: Any,  # PipelineContext lives in processing/, kept untyped here
    ) -> list[NormalizedMention]: ...
