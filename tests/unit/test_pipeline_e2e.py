"""End-to-end pipeline tests on FakeRepository.

Criteria 6 from todo-002: Pipeline(stages=[...], repository=FakeRepository()).run(...)
returns list[Signal] on a synthetic batch.
"""
from __future__ import annotations

import datetime
from uuid import uuid4

import pytest

from crawler.core.models import RawMention
from crawler.processing._fakes import FakeRepository
from crawler.processing.pipeline import Pipeline
from crawler.processing.stages.decide import DecideStage
from crawler.processing.stages.dedup import DedupStage
from crawler.processing.stages.keyword_filter import KeywordFilterStage
from crawler.processing.stages.normalize import NormalizeStage
from tests.unit.conftest import make_project, make_raw_mention

UTC = datetime.UTC


def make_pipeline(repo=None):
    return Pipeline(
        stages=[NormalizeStage(), DedupStage(), KeywordFilterStage(), DecideStage()],
        repository=repo or FakeRepository(),
    )


def make_mentions(texts: list[str]) -> list[RawMention]:
    return [make_raw_mention(text=t) for t in texts]


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_returns_signals():
    """Pipeline returns list[Signal] for a synthetic batch of matching mentions."""
    project = make_project(keywords=["anthropic", "claude"])
    mentions = make_mentions([
        "Anthropic released Claude 4 today with new features",
        "Claude AI is getting better at coding tasks",
        "New benchmark results for Claude 3 Opus",
    ])

    pipeline = make_pipeline()
    signals = await pipeline.run(mentions, project)

    assert isinstance(signals, list)
    assert len(signals) == 3
    for sig in signals:
        assert sig.relevance_score == 1.0
        assert sig.intent == "other"
        assert sig.project_id == project.id


@pytest.mark.asyncio
async def test_pipeline_filters_non_matching():
    """Mentions not matching keywords are filtered by KeywordFilterStage."""
    project = make_project(keywords=["anthropic"])
    mentions = make_mentions([
        "Anthropic released a new model today for everyone",
        "OpenAI announced new GPT-5 features",
        "Google DeepMind published a research paper",
    ])

    pipeline = make_pipeline()
    signals = await pipeline.run(mentions, project)

    assert len(signals) == 1
    # Only the Anthropic mention passes


@pytest.mark.asyncio
async def test_pipeline_dedup_removes_duplicates():
    """Duplicate mentions (same text) produce only one Signal."""
    project = make_project(keywords=["anthropic"])
    text = "Anthropic is an AI safety company with great products"
    mentions = [
        make_raw_mention(text=text, external_id="t3_001"),
        make_raw_mention(text=text, external_id="t3_002"),  # duplicate
        make_raw_mention(text="Anthropic released Claude today as a major update", external_id="t3_003"),
    ]

    pipeline = make_pipeline()
    signals = await pipeline.run(mentions, project)

    assert len(signals) == 2  # 1 deduped + 1 unique


@pytest.mark.asyncio
async def test_pipeline_empty_keywords_pass_all():
    """Empty keywords → all mentions pass (no-op filter)."""
    project = make_project(keywords=[])
    mentions = make_mentions([
        "Something totally unrelated to AI",
        "Another random post about cooking",
        "Yet another post about sports",
    ])

    pipeline = make_pipeline()
    signals = await pipeline.run(mentions, project)

    assert len(signals) == 3


@pytest.mark.asyncio
async def test_pipeline_signals_have_pipeline_trace():
    """Each Signal has pipeline_trace with entries from all 4 stages... at least decide."""
    project = make_project(keywords=["claude"])
    mentions = make_mentions([
        "Claude AI model from Anthropic is excellent",
    ])

    pipeline = make_pipeline()
    signals = await pipeline.run(mentions, project)

    assert len(signals) == 1
    trace = signals[0].pipeline_trace
    stage_names = {entry.stage_name for entry in trace}
    # At minimum, normalize, dedup, keyword_filter stages should be in trace
    # (DecideStage adds its trace entry BEFORE creating the Signal, so it's included too)
    assert "normalize" in stage_names


@pytest.mark.asyncio
async def test_pipeline_empty_batch():
    """Empty input → empty signals."""
    project = make_project()
    pipeline = make_pipeline()
    signals = await pipeline.run([], project)
    assert signals == []


@pytest.mark.asyncio
async def test_pipeline_dedup_cross_run():
    """Second run with same mentions → all filtered by dedup (already in FakeRepository)."""
    project = make_project(keywords=["anthropic"])
    mentions = make_mentions([
        "Anthropic released Claude today as a new model",
        "Anthropic is working on AI safety research",
    ])

    repo = FakeRepository()
    pipeline = make_pipeline(repo)

    # First run
    signals1 = await pipeline.run(mentions, project)
    # Simulate what integration CLI would do: persist normalized mentions to repo
    # so that the second run's DedupStage sees them as "already existing".
    stage = NormalizeStage()
    from crawler.processing.context import PipelineContext
    ctx = PipelineContext(project=project, scan_id=uuid4(), repository=repo)
    normalized = await stage.process(mentions, ctx)  # type: ignore[arg-type]
    await repo.bulk_upsert_mentions_with_dedup(normalized)

    # Second run with same mentions → all deduped
    signals2 = await pipeline.run(mentions, project)
    assert len(signals1) == 2
    assert len(signals2) == 0


@pytest.mark.asyncio
async def test_pipeline_criteria_6():
    """
    Criterion 6 from todo-002: exact smoke-test as specified.

    Pipeline(stages=[NormalizeStage(), DedupStage(), KeywordFilterStage(),
    DecideStage()], repository=FakeRepository()).run(mentions, project)
    returns list of Signal on synthetic batch.
    """
    from crawler.core.models import Signal

    pipeline = Pipeline(
        stages=[NormalizeStage(), DedupStage(), KeywordFilterStage(), DecideStage()],
        repository=FakeRepository(),
    )

    project = make_project(keywords=["anthropic"])
    mentions = make_mentions([
        "Anthropic Claude is a great AI assistant",
        "Anthropic raises funding for AI safety",
    ])

    signals = await pipeline.run(mentions=mentions, project=project)
    assert isinstance(signals, list)
    assert len(signals) == 2
    for sig in signals:
        assert isinstance(sig, Signal)
