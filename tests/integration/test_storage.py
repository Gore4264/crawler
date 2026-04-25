"""Integration smoke tests for crawler.storage on a real Postgres.

Each test exercises one E1-scope IRepository method end-to-end. The
fixtures live in tests/conftest.py.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import HttpUrl

from crawler.core.events import DomainEvent  # noqa: F401  (smoke import)
from crawler.core.models import (
    Intent,
    NormalizedMention,
    PipelineTraceEntry,
    Signal,
)
from crawler.storage import Database, Repository
from crawler.storage.migrate import run_migrations


def _utc(*args, **kwargs) -> datetime:
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


def _make_mention(seed: int) -> NormalizedMention:
    """Build a NormalizedMention with deterministic content for the given
    seed. content_hash is sha256(text_clean) so equal seeds collide."""
    text = f"sample mention number {seed}"
    text_clean = text  # already normalized for the test
    content_hash = hashlib.sha256(text_clean.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    return NormalizedMention(
        source_id="test_source",
        external_id=f"ext-{seed}",
        author=f"author-{seed}",
        author_id=f"aid-{seed}",
        text=text,
        text_html=None,
        url=HttpUrl(f"https://example.com/post/{seed}"),
        lang_hint="en",
        engagement={"likes": seed},
        raw={"seed": seed},
        published_at=now - timedelta(minutes=10),
        discovered_at=now - timedelta(minutes=5),
        fetched_at=now,
        text_clean=text_clean,
        lang="en",
        content_hash=content_hash,
        is_html_stripped=False,
        normalize_version=1,
        tracking_params_removed=[],
    )


def _make_signal(mention: NormalizedMention, *, intent: Intent = "discussion",
                 score: float = 0.8, project_id: str = "demo") -> Signal:
    now = datetime.now(timezone.utc)
    trace = [
        PipelineTraceEntry(
            stage_name="normalize",
            started_at=now - timedelta(seconds=2),
            duration_ms=10,
            items_in=1,
            items_out=1,
        )
    ]
    return Signal(
        mention_id=mention.id,
        project_id=project_id,
        matched_query="brand_query",
        relevance_score=score,
        is_spam=False,
        intent=intent,
        sentiment="neutral",
        entities=["egor"],
        topics=["news"],
        pipeline_trace=trace,
        cost_usd=Decimal("0.01"),
        created_at=now,
    )


# ----- Migration tests --------------------------------------------------------


async def test_migration_applies_cleanly(database: Database) -> None:
    """The session fixture already ran migrations — verify the four E1
    tables are present in the catalog."""
    async with database.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "  AND table_name IN ('mentions','signals','scan_log','usage_log')"
        )
    names = {r["table_name"] for r in rows}
    assert names == {"mentions", "signals", "scan_log", "usage_log"}


async def test_migration_idempotent(database: Database) -> None:
    """Running the runner again is a no-op (every migration is recorded)."""
    applied = await run_migrations(database)
    assert applied == []


# ----- Mentions ---------------------------------------------------------------


async def test_bulk_upsert_dedup(db: Database) -> None:
    repo = Repository(db)
    mentions = [_make_mention(i) for i in range(10)]
    inserted, skipped = await repo.bulk_upsert_mentions_with_dedup(mentions)
    assert (inserted, skipped) == (10, 0)
    inserted2, skipped2 = await repo.bulk_upsert_mentions_with_dedup(mentions)
    assert (inserted2, skipped2) == (0, 10)


async def test_existing_hashes(db: Database) -> None:
    repo = Repository(db)
    mentions = [_make_mention(i) for i in range(10)]
    await repo.bulk_upsert_mentions_with_dedup(mentions)
    hashes = [m.content_hash for m in mentions]
    found = await repo.existing_hashes(hashes)
    assert found == set(hashes)
    random_hashes = [hashlib.sha256(f"nope-{i}".encode()).hexdigest()
                     for i in range(5)]
    assert await repo.existing_hashes(random_hashes) == set()


# ----- Signals ----------------------------------------------------------------


async def test_insert_and_get_signal(db: Database) -> None:
    repo = Repository(db)
    mention = _make_mention(1)
    await repo.bulk_upsert_mentions_with_dedup([mention])
    # 0.75 is exactly representable in REAL (float32), so the round-trip
    # is bit-for-bit equal. Arbitrary decimals like 0.8 widen on read.
    signal = _make_signal(mention, score=0.75)
    inserted = await repo.insert_signals([signal])
    assert inserted == 1
    got = await repo.get_signal(signal.id)
    assert got is not None
    # Field-by-field — Pydantic equality would fail on tzinfo identity
    # inside pipeline_trace (UTC after json round-trip is the same offset
    # but a different class) and on Decimal scale (NUMERIC(12,6) returns
    # 0.010000 where input was 0.01; arithmetically equal, structurally
    # the same string-form survives via model_dump).
    assert got.id == signal.id
    assert got.mention_id == signal.mention_id
    assert got.project_id == signal.project_id
    assert got.matched_query == signal.matched_query
    assert got.relevance_score == signal.relevance_score
    assert got.is_spam == signal.is_spam
    assert got.intent == signal.intent
    assert got.sentiment == signal.sentiment
    assert got.entities == signal.entities
    assert got.topics == signal.topics
    assert got.cost_usd == signal.cost_usd
    assert got.created_at == signal.created_at
    assert len(got.pipeline_trace) == len(signal.pipeline_trace)
    assert got.pipeline_trace[0].stage_name == signal.pipeline_trace[0].stage_name
    assert got.pipeline_trace[0].duration_ms == signal.pipeline_trace[0].duration_ms
    assert got.pipeline_trace[0].started_at == signal.pipeline_trace[0].started_at


async def test_search_signals_basic(db: Database) -> None:
    repo = Repository(db)
    mentions = [_make_mention(i) for i in range(3)]
    await repo.bulk_upsert_mentions_with_dedup(mentions)
    signals = [
        _make_signal(mentions[0], intent="complaint", score=0.9),
        _make_signal(mentions[1], intent="discussion", score=0.6),
        _make_signal(mentions[2], intent="discussion", score=0.4),
    ]
    await repo.insert_signals(signals)

    all_for_demo = await repo.search_signals("demo")
    assert len(all_for_demo) == 3

    only_complaints = await repo.search_signals("demo", intent="complaint")
    assert len(only_complaints) == 1
    assert only_complaints[0].intent == "complaint"

    high_score = await repo.search_signals("demo", min_score=0.7)
    assert len(high_score) == 1
    assert high_score[0].relevance_score >= 0.7

    other_project = await repo.search_signals("other_project")
    assert other_project == []


# ----- Scan log ---------------------------------------------------------------


async def test_scan_log_record_and_last_scanned(db: Database) -> None:
    repo = Repository(db)
    project, source, query = "demo", "test_source", "brand_query"

    assert await repo.last_scanned_at(project, source, query) is None

    base = _utc(2026, 4, 25, 10, 0)
    await repo.record_scan(
        scan_id=uuid4(),
        project_id=project,
        source_id=source,
        query_name=query,
        started_at=base,
        finished_at=base + timedelta(minutes=2),
        count=5,
        cost_usd=Decimal("0.01"),
        status="ok",
    )
    await repo.record_scan(
        scan_id=uuid4(),
        project_id=project,
        source_id=source,
        query_name=query,
        started_at=base + timedelta(hours=1),
        finished_at=base + timedelta(hours=1, minutes=2),
        count=3,
        cost_usd=Decimal("0.005"),
        status="partial",
    )
    # A failed scan must NOT advance last_scanned_at (partial index filter).
    await repo.record_scan(
        scan_id=uuid4(),
        project_id=project,
        source_id=source,
        query_name=query,
        started_at=base + timedelta(hours=2),
        finished_at=base + timedelta(hours=2, minutes=1),
        count=0,
        cost_usd=Decimal("0"),
        status="failed",
    )

    last = await repo.last_scanned_at(project, source, query)
    assert last == base + timedelta(hours=1, minutes=2)


# ----- Usage / budget ---------------------------------------------------------


async def test_usage_log_and_budget(db: Database) -> None:
    repo = Repository(db)
    project = "demo"
    base = _utc(2026, 4, 25, 0, 0)

    await repo.append_usage(
        project_id=project,
        source_id="reddit",
        cost_usd=Decimal("0.10"),
        occurred_at=base,
        kind="source",
    )
    await repo.append_usage(
        project_id=project,
        source_id="reddit",
        cost_usd=Decimal("0.05"),
        occurred_at=base + timedelta(hours=1),
        kind="embedding",
    )
    await repo.append_usage(
        project_id=project,
        source_id="bluesky",
        cost_usd=Decimal("0.02"),
        occurred_at=base + timedelta(hours=2),
        kind="source",
    )

    total = await repo.budget_used(project, since=base - timedelta(days=1))
    assert total == Decimal("0.17")

    reddit_only = await repo.budget_used_by_source(
        project, "reddit", since=base - timedelta(days=1)
    )
    assert reddit_only == Decimal("0.15")

    windowed = await repo.budget_used(
        project,
        since=base + timedelta(minutes=30),
        until=base + timedelta(hours=1, minutes=30),
    )
    assert windowed == Decimal("0.05")


# ----- NotImplementedError stubs ---------------------------------------------


async def test_non_e1_methods_raise_not_implemented(db: Database) -> None:
    """Out-of-E1 methods must raise NotImplementedError so callers can't
    silently use unfinished functionality (storage/CLAUDE.md C.5)."""
    repo = Repository(db)
    with pytest.raises(NotImplementedError):
        await repo.search_hybrid("demo", "text", [0.0] * 1024)
    with pytest.raises(NotImplementedError):
        await repo.get_project("demo")
    with pytest.raises(NotImplementedError):
        await repo.list_projects()
