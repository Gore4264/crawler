"""Unit tests for DedupStage."""
from __future__ import annotations

import datetime
from uuid import uuid4

import pytest

from crawler.processing._fakes import FakeRepository
from crawler.processing.context import PipelineContext
from crawler.processing.stages.dedup import DedupStage
from crawler.processing.stages.normalize import NormalizeStage
from tests.unit.conftest import make_project, make_raw_mention

UTC = datetime.UTC


def make_ctx(repo=None):
    return PipelineContext(
        project=make_project(),
        scan_id=uuid4(),
        repository=repo or FakeRepository(),
    )


async def normalize_mentions(mentions):
    """Helper: run mentions through NormalizeStage to get NormalizedMentions."""
    stage = NormalizeStage()
    ctx = make_ctx()
    return await stage.process(mentions, ctx)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# In-batch dedup: first-wins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbatch_dedup_first_wins():
    """Two mentions with identical text → only the first is kept."""
    text = "Duplicate content about Anthropic Claude"
    datetime.datetime.now(UTC)

    m1 = make_raw_mention(text=text, external_id="t3_aaa")
    m2 = make_raw_mention(text=text, external_id="t3_bbb")

    normalized = await normalize_mentions([m1, m2])
    assert normalized[0].content_hash == normalized[1].content_hash

    dedup = DedupStage()
    repo = FakeRepository()
    ctx = make_ctx(repo)

    result = await dedup.process(normalized, ctx)
    assert len(result) == 1
    assert result[0].external_id == "t3_aaa"  # first wins


@pytest.mark.asyncio
async def test_inbatch_dedup_different_text():
    """Two mentions with different text → both pass."""
    m1 = make_raw_mention(text="Unique content A about Anthropic")
    m2 = make_raw_mention(text="Unique content B about Claude AI")

    normalized = await normalize_mentions([m1, m2])

    dedup = DedupStage()
    ctx = make_ctx()
    result = await dedup.process(normalized, ctx)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Filtering against FakeRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_filters_against_existing_hashes():
    """Mentions already in the repository are filtered out."""
    m1 = make_raw_mention(text="Previously seen content about machine learning")
    m2 = make_raw_mention(text="Brand new content about generative AI")

    normalized = await normalize_mentions([m1, m2])

    repo = FakeRepository()
    # Pre-populate the repository with m1's hash
    await repo.bulk_upsert_mentions_with_dedup([normalized[0]])

    dedup = DedupStage()
    ctx = make_ctx(repo)
    result = await dedup.process(normalized, ctx)

    assert len(result) == 1
    assert result[0].content_hash == normalized[1].content_hash


@pytest.mark.asyncio
async def test_dedup_all_new():
    """All mentions are new → all pass through."""
    mentions = [make_raw_mention(text=f"Fresh content number {i} on AI") for i in range(3)]
    normalized = await normalize_mentions(mentions)

    dedup = DedupStage()
    ctx = make_ctx()
    result = await dedup.process(normalized, ctx)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_dedup_all_existing():
    """All mentions already in repository → empty result."""
    mentions = [make_raw_mention(text=f"Old content number {i} on AI") for i in range(3)]
    normalized = await normalize_mentions(mentions)

    repo = FakeRepository()
    await repo.bulk_upsert_mentions_with_dedup(normalized)

    dedup = DedupStage()
    ctx = make_ctx(repo)
    result = await dedup.process(normalized, ctx)
    assert result == []


@pytest.mark.asyncio
async def test_dedup_empty_input():
    """Empty input → empty output."""
    dedup = DedupStage()
    ctx = make_ctx()
    result = await dedup.process([], ctx)
    assert result == []
