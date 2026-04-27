"""Unit tests for KeywordFilterStage."""
from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from crawler.core.models import BudgetConfig, NotificationConfig, Project, TopicQuery
from crawler.processing._fakes import FakeRepository
from crawler.processing.context import PipelineContext
from crawler.processing.stages.keyword_filter import KeywordFilterStage, _compile_keyword
from crawler.processing.stages.normalize import NormalizeStage
from tests.unit.conftest import make_project, make_raw_mention

UTC = datetime.UTC


def make_project_with_keywords(
    keywords: list[str],
    excluded_keywords: list[str] | None = None,
    project_id: str = "test",
) -> Project:
    return Project(
        id=project_id,
        name="Test",
        queries=[
            TopicQuery(
                name="main",
                keywords=keywords,
                excluded_keywords=excluded_keywords or [],
            )
        ],
        sources=["reddit"],
        notifications=[NotificationConfig(channel="telegram", target="123")],
        budget=BudgetConfig(monthly_usd=Decimal("10.00")),
        pipeline=["normalize", "dedup", "keyword_filter", "decide"],
        schedule_default="0 * * * *",
    )


def make_ctx(project):
    return PipelineContext(
        project=project,
        scan_id=uuid4(),
        repository=FakeRepository(),
    )


async def normalize(text: str, project=None):
    """Helper: normalize a single mention and return it."""
    stage = NormalizeStage()
    mention = make_raw_mention(text=text)
    ctx = PipelineContext(
        project=project or make_project(),
        scan_id=uuid4(),
        repository=FakeRepository(),
    )
    results = await stage.process([mention], ctx)  # type: ignore[arg-type]
    return results[0]


# ---------------------------------------------------------------------------
# Compile keyword tests
# ---------------------------------------------------------------------------


def test_compile_long_word_uses_word_boundary():
    """Words > 3 chars get word-boundary regex."""
    pat = _compile_keyword("anthropic")
    assert pat.search("anthropic")  # matches
    assert not pat.search("anthropics")  # does NOT match (word boundary)


def test_compile_short_word_uses_substring():
    """Words ≤ 3 chars get substring match (no boundary)."""
    pat = _compile_keyword("AI")
    assert pat.search("AI-driven")  # substring match inside hyphenated word
    assert pat.search("train AI models")


def test_compile_multiword_phrase():
    """Multi-word phrase uses verbatim substring."""
    pat = _compile_keyword("machine learning")
    assert pat.search("deep machine learning techniques")
    assert not pat.search("machine  learning")  # double space breaks exact match


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_word_boundary_match():
    """'anthropic' keyword matches text containing the word."""
    project = make_project_with_keywords(["anthropic"])
    norm = await normalize("Anthropic released a new Claude model", project)

    kf = KeywordFilterStage()
    ctx = make_ctx(project)
    result = await kf.process([norm], ctx)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_word_boundary_no_match():
    """Text without keyword is filtered out."""
    project = make_project_with_keywords(["anthropic"])
    norm = await normalize("OpenAI released GPT-5 today", project)

    kf = KeywordFilterStage()
    ctx = make_ctx(project)
    result = await kf.process([norm], ctx)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_short_keyword_substring():
    """Short keyword 'AI' is a substring match."""
    project = make_project_with_keywords(["AI"])
    norm = await normalize("The AI-powered assistant is helpful", project)

    kf = KeywordFilterStage()
    ctx = make_ctx(project)
    result = await kf.process([norm], ctx)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_multiword_keyword():
    """Multi-word phrase 'machine learning' is a verbatim substring."""
    project = make_project_with_keywords(["machine learning"])
    norm = await normalize("Deep machine learning techniques for NLP", project)

    kf = KeywordFilterStage()
    ctx = make_ctx(project)
    result = await kf.process([norm], ctx)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_excluded_keywords():
    """Mention matches include keyword but also excluded → filtered out."""
    project = make_project_with_keywords(
        keywords=["anthropic"],
        excluded_keywords=["spam"],
    )
    norm = await normalize("Anthropic spam promotion buy now", project)

    kf = KeywordFilterStage()
    ctx = make_ctx(project)
    result = await kf.process([norm], ctx)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_excluded_keywords_no_effect_when_absent():
    """Excluded keyword not present → mention still passes."""
    project = make_project_with_keywords(
        keywords=["anthropic"],
        excluded_keywords=["spam"],
    )
    norm = await normalize("Anthropic released a helpful new model", project)

    kf = KeywordFilterStage()
    ctx = make_ctx(project)
    result = await kf.process([norm], ctx)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_empty_keywords_noop():
    """Empty keywords → no-op, all mentions pass through (decision F.1)."""
    project = make_project_with_keywords(keywords=[], excluded_keywords=[])
    norm = await normalize("Completely unrelated content about cooking recipes", project)

    kf = KeywordFilterStage()
    ctx = make_ctx(project)
    result = await kf.process([norm], ctx)
    assert len(result) == 1  # passes because no keywords = no filter


@pytest.mark.asyncio
async def test_multiple_keywords_or_semantics():
    """Multiple include keywords use OR semantics — any match suffices."""
    project = make_project_with_keywords(["claude", "gpt"])
    norm1 = await normalize("Claude is great for coding", project)
    norm2 = await normalize("GPT-4 was released by OpenAI", project)
    norm3 = await normalize("No AI keywords here, just cooking", project)

    kf = KeywordFilterStage()
    ctx = make_ctx(project)
    result = await kf.process([norm1, norm2, norm3], ctx)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_pattern_cache_reuse():
    """Same project called twice → patterns compiled once (cache works)."""
    project = make_project_with_keywords(["anthropic"])
    kf = KeywordFilterStage()

    for _ in range(3):
        norm = await normalize("Anthropic released Claude today", project)
        ctx = make_ctx(project)
        result = await kf.process([norm], ctx)
        assert len(result) == 1

    # Only one entry in cache for this project
    assert project.id in kf._compiled
    assert len(kf._compiled) == 1
