"""Pydantic v2 domain models — section A of core/CLAUDE.md.

All models are frozen value-objects with `extra="forbid"`. All datetime fields
are tz-aware UTC; naive datetimes are rejected at validation. All money values
are `decimal.Decimal`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

# --- A.1. Type aliases -------------------------------------------------------

SourceMode = Literal["search", "stream"]
Intent = Literal[
    "complaint",
    "question",
    "recommendation",
    "advertisement",
    "news",
    "discussion",
    "other",
]
Sentiment = Literal["positive", "neutral", "negative"]
NotificationChannel = Literal["telegram", "webhook", "email"]
CostModel = Literal["free", "per_request", "per_result", "subscription"]
ScanStatus = Literal["ok", "partial", "failed"]
NotificationStatus = Literal["ok", "failed", "skipped"]
FeedbackKind = Literal["relevant", "noise", "block_author"]
UsageKind = Literal["source", "embedding", "llm", "other"]
BudgetScope = Literal["monthly", "daily", "per_source"]


# --- Helper validator: tz-aware UTC enforcement ------------------------------


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be tz-aware (got naive datetime)")
    if value.utcoffset() != UTC.utcoffset(value):
        return value.astimezone(UTC)
    return value


_SLUG_TOPIC_RE = re.compile(r"[a-z0-9_]+")
_SLUG_PROJECT_RE = re.compile(r"[a-z0-9_-]+")


# --- A.2. SourceQuery --------------------------------------------------------


class SourceQuery(BaseModel):
    """Unified source request. mode='search' is REST-pull, mode='stream' is
    long-lived (see ADR-0002)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: SourceMode = "search"
    keywords: list[str] = Field(default_factory=list)
    semantic_query: str | None = None
    excluded_keywords: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    geo: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    since_cursor: str | None = None
    limit: int = 100
    max_cost_usd: Decimal = Decimal("1.00")

    @field_validator("since", "until", mode="after")
    @classmethod
    def _validate_tz(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _ensure_utc(v)

    @model_validator(mode="after")
    def _validate_window(self) -> SourceQuery:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be greater than since")
        if self.mode == "stream" and self.until is not None:
            raise ValueError("stream mode is open-ended; until is not allowed")
        return self


# --- A.3. RawMention ---------------------------------------------------------


class RawMention(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    external_id: str
    author: str | None = None
    author_id: str | None = None
    text: str = Field(min_length=1)
    text_html: str | None = None
    url: HttpUrl
    lang_hint: str | None = None
    engagement: dict[str, int] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    published_at: datetime
    discovered_at: datetime
    fetched_at: datetime

    @field_validator("text", mode="after")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("text must be non-empty after strip")
        return s

    @field_validator("published_at", "discovered_at", "fetched_at", mode="after")
    @classmethod
    def _validate_tz(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


# --- A.4. NormalizedMention --------------------------------------------------


class NormalizedMention(RawMention):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    text_clean: str
    lang: str
    content_hash: str
    is_html_stripped: bool
    normalize_version: int = 1
    tracking_params_removed: list[str] = Field(default_factory=list)
    minhash_signature: list[int] | None = None
    embedding: list[float] | None = None

    @model_validator(mode="after")
    def _validate_hash_and_embedding(self) -> NormalizedMention:
        if len(self.content_hash) != 64:
            raise ValueError("content_hash must be 64-char hex")
        try:
            int(self.content_hash, 16)
        except ValueError as e:
            raise ValueError("content_hash must be hex") from e
        if self.embedding is not None and len(self.embedding) != 1024:
            raise ValueError("embedding must be 1024-dim (ADR-0001)")
        return self


# --- A.5. PipelineTraceEntry -------------------------------------------------


class PipelineTraceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage_name: str
    started_at: datetime
    duration_ms: int
    items_in: int
    items_out: int
    cost_usd: Decimal = Decimal("0")
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("started_at", mode="after")
    @classmethod
    def _validate_tz(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


# --- A.6. Signal -------------------------------------------------------------


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    mention_id: UUID
    project_id: str
    matched_query: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    is_spam: bool
    intent: Intent
    sentiment: Sentiment
    entities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    pipeline_trace: list[PipelineTraceEntry] = Field(min_length=1)
    cost_usd: Decimal = Decimal("0")
    created_at: datetime

    @field_validator("created_at", mode="after")
    @classmethod
    def _validate_tz(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


# --- A.7. TopicQuery ---------------------------------------------------------


class TopicQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    semantic: str | None = None
    languages: list[str] = Field(default_factory=list)
    geo: str | None = None
    sources: list[str] = Field(default_factory=list)
    schedule: str | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    topic_embedding: list[float] | None = None

    @model_validator(mode="after")
    def _validate(self) -> TopicQuery:
        if not _SLUG_TOPIC_RE.fullmatch(self.name):
            raise ValueError("TopicQuery.name must be a slug ([a-z0-9_]+)")
        if self.topic_embedding is not None and len(self.topic_embedding) != 1024:
            raise ValueError("topic_embedding must be 1024-dim (ADR-0001)")
        return self


# --- A.8. BudgetConfig -------------------------------------------------------


class BudgetConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    monthly_usd: Decimal
    daily_usd: Decimal | None = None
    per_source_usd: dict[str, Decimal] = Field(default_factory=dict)
    warning_threshold: float = 0.8
    cutoff_threshold: float = 0.95

    @model_validator(mode="after")
    def _validate_thresholds(self) -> BudgetConfig:
        if not (0.0 < self.warning_threshold < self.cutoff_threshold <= 1.0):
            raise ValueError("must satisfy 0 < warning < cutoff <= 1")
        if self.monthly_usd <= 0:
            raise ValueError("monthly_usd must be positive")
        return self


# --- A.9. NotificationConfig -------------------------------------------------


class NotificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: NotificationChannel
    target: str
    filter_expr: str | None = None
    dedup_window_seconds: int | None = None


# --- A.10. Project -----------------------------------------------------------


class Project(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    queries: list[TopicQuery]
    sources: list[str]
    notifications: list[NotificationConfig]
    budget: BudgetConfig
    pipeline: list[str | dict[str, Any]]
    schedule_default: str
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> Project:
        if not _SLUG_PROJECT_RE.fullmatch(self.id):
            raise ValueError("Project.id must be a slug ([a-z0-9_-]+)")
        names = [q.name for q in self.queries]
        if len(names) != len(set(names)):
            raise ValueError("TopicQuery.name must be unique within Project")
        return self
