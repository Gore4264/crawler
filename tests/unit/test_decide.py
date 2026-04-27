"""Unit tests for DecideStage."""
from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from crawler.core.models import PipelineTraceEntry
from crawler.processing._fakes import FakeRepository
from crawler.processing.context import PipelineContext
from crawler.processing.stages.decide import DecideStage
from crawler.processing.stages.normalize import NormalizeStage
from tests.unit.conftest import make_project, make_raw_mention

UTC = datetime.UTC


async def make_normalized(text: str = "Anthropic released a new model today"):
    stage = NormalizeStage()
    mention = make_raw_mention(text=text)
    ctx = PipelineContext(
        project=make_project(),
        scan_id=uuid4(),
        repository=FakeRepository(),
    )
    result = await stage.process([mention], ctx)  # type: ignore[arg-type]
    return result[0]


def make_ctx(project=None, with_trace=True):
    ctx = PipelineContext(
        project=project or make_project(),
        scan_id=uuid4(),
        repository=FakeRepository(),
    )
    if with_trace:
        # Add a synthetic trace entry to satisfy Signal's min_length=1 requirement
        ctx.trace.append(
            PipelineTraceEntry(
                stage_name="normalize",
                started_at=datetime.datetime.now(UTC),
                duration_ms=5,
                items_in=1,
                items_out=1,
            )
        )
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_creates_signal():
    """DecideStage creates one Signal per surviving mention."""
    norm = await make_normalized()
    ctx = make_ctx()

    stage = DecideStage()
    result = await stage.process([norm], ctx)

    assert result == []  # DecideStage returns empty list
    assert len(ctx.pending_signals) == 1


@pytest.mark.asyncio
async def test_decide_signal_fields():
    """Signal has correct synthetic fields."""
    norm = await make_normalized()
    project = make_project()
    ctx = make_ctx(project)

    stage = DecideStage()
    await stage.process([norm], ctx)

    signal = ctx.pending_signals[0]
    assert signal.mention_id == norm.id
    assert signal.project_id == project.id
    assert signal.matched_query == project.queries[0].name
    assert signal.relevance_score == 1.0
    assert signal.is_spam is False
    assert signal.intent == "other"
    assert signal.sentiment == "neutral"
    assert signal.entities == []
    assert signal.topics == []
    assert signal.cost_usd == Decimal("0")


@pytest.mark.asyncio
async def test_decide_pipeline_trace_in_signal():
    """Signal contains a copy of ctx.trace at time of creation."""
    norm = await make_normalized()
    ctx = make_ctx(with_trace=True)

    stage = DecideStage()
    await stage.process([norm], ctx)

    signal = ctx.pending_signals[0]
    assert len(signal.pipeline_trace) >= 1
    assert signal.pipeline_trace[0].stage_name == "normalize"


@pytest.mark.asyncio
async def test_decide_multiple_mentions():
    """DecideStage creates one Signal per mention."""
    mentions = [await make_normalized(f"Unique text number {i} about AI") for i in range(5)]
    ctx = make_ctx()

    stage = DecideStage()
    result = await stage.process(mentions, ctx)

    assert result == []
    assert len(ctx.pending_signals) == 5


@pytest.mark.asyncio
async def test_decide_empty_input():
    """Empty input → no signals created."""
    ctx = make_ctx()
    stage = DecideStage()
    result = await stage.process([], ctx)
    assert result == []
    assert ctx.pending_signals == []


@pytest.mark.asyncio
async def test_decide_returns_empty_list_not_none():
    """DecideStage returns [] (not None), so pipeline can check len(current)."""
    norm = await make_normalized()
    ctx = make_ctx()
    stage = DecideStage()
    result = await stage.process([norm], ctx)
    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_decide_matched_query_uses_first_query():
    """matched_query is the name of project.queries[0]."""
    norm = await make_normalized()
    project = make_project()
    ctx = make_ctx(project)

    stage = DecideStage()
    await stage.process([norm], ctx)

    assert ctx.pending_signals[0].matched_query == "main_topic"
