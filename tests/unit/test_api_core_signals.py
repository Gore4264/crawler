"""Unit tests for api_core/signals.py.

All tests run against FakeRepository — no real Postgres required.
"""
from __future__ import annotations

import datetime
import hashlib
from decimal import Decimal
from uuid import uuid4

import pytest

from crawler.api_core import signals as signals_api
from crawler.core.models import NormalizedMention, PipelineTraceEntry, Signal
from crawler.processing._fakes import FakeRepository
from tests.unit.conftest import make_raw_mention

UTC = datetime.UTC


def _make_normalized_mention(text: str = "hello world from anthropic") -> NormalizedMention:
    raw = make_raw_mention(text=text)
    text_clean = text.lower()
    return NormalizedMention(
        **raw.model_dump(),
        text_clean=text_clean,
        lang="en",
        content_hash=hashlib.sha256(text_clean.encode("utf-8")).hexdigest(),
        is_html_stripped=False,
        normalize_version=1,
        tracking_params_removed=[],
    )


def _make_trace_entry() -> PipelineTraceEntry:
    return PipelineTraceEntry(
        stage_name="decide",
        started_at=datetime.datetime.now(UTC),
        duration_ms=1,
        items_in=1,
        items_out=1,
        cost_usd=Decimal("0"),
    )


def _make_signal(project_id: str, mention_id=None) -> Signal:
    return Signal(
        id=uuid4(),
        mention_id=mention_id or uuid4(),
        project_id=project_id,
        matched_query="main",
        relevance_score=1.0,
        is_spam=False,
        intent="other",
        sentiment="neutral",
        entities=[],
        topics=[],
        pipeline_trace=[_make_trace_entry()],
        cost_usd=Decimal("0"),
        created_at=datetime.datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# search_signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_signals_returns_project_signals():
    repo = FakeRepository()
    sig = _make_signal("proj-a")
    _other = _make_signal("proj-b")
    await repo.insert_signals([sig, _other])

    results = await signals_api.search_signals(repo, "proj-a")
    assert len(results) == 1
    assert results[0].project_id == "proj-a"


@pytest.mark.asyncio
async def test_search_signals_empty():
    repo = FakeRepository()
    results = await signals_api.search_signals(repo, "no-project")
    assert results == []


@pytest.mark.asyncio
async def test_search_signals_with_text_query():
    """Text query filters by substring in mention text_clean."""
    repo = FakeRepository()
    mention = _make_normalized_mention("anthropic released claude today")
    await repo.bulk_upsert_mentions_with_dedup([mention])
    sig = _make_signal("proj-a", mention_id=mention.id)
    await repo.insert_signals([sig])

    # Matching query
    results = await signals_api.search_signals(repo, "proj-a", text_query="claude")
    assert len(results) == 1

    # Non-matching query
    results_none = await signals_api.search_signals(repo, "proj-a", text_query="openai")
    assert len(results_none) == 0


# ---------------------------------------------------------------------------
# count_signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_signals_correct():
    repo = FakeRepository()
    await repo.insert_signals([_make_signal("p1"), _make_signal("p1"), _make_signal("p2")])
    count = await signals_api.count_signals(repo, "p1")
    assert count == 2


@pytest.mark.asyncio
async def test_count_signals_zero():
    repo = FakeRepository()
    assert await signals_api.count_signals(repo, "none") == 0


# ---------------------------------------------------------------------------
# get_signal_with_mention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_signal_with_mention_found():
    repo = FakeRepository()
    mention = _make_normalized_mention("test signal mention")
    await repo.bulk_upsert_mentions_with_dedup([mention])
    sig = _make_signal("proj", mention_id=mention.id)
    await repo.insert_signals([sig])

    result = await signals_api.get_signal_with_mention(repo, sig.id)
    assert result is not None
    s, m = result
    assert s.id == sig.id
    assert m.id == mention.id


@pytest.mark.asyncio
async def test_get_signal_with_mention_not_found():
    repo = FakeRepository()
    result = await signals_api.get_signal_with_mention(repo, uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# get_usage_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_usage_summary_empty():
    repo = FakeRepository()
    since = datetime.datetime.now(UTC) - datetime.timedelta(days=30)
    summary = await signals_api.get_usage_summary(repo, "proj", since=since)
    assert summary.total_usd == Decimal("0")
    assert summary.signals_count == 0
    assert summary.cost_per_signal is None
