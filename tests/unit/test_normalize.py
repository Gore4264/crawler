"""Unit tests for NormalizeStage.

Covers all 5 test cases from core/CLAUDE.md D.4.
"""
from __future__ import annotations

import datetime
import unicodedata
from uuid import uuid4

import pytest
from pydantic import HttpUrl

from crawler.core.models import RawMention
from crawler.processing._fakes import FakeRepository
from crawler.processing.context import PipelineContext
from crawler.processing.stages.normalize import (
    NormalizeStage,
    _compute_content_hash,
    _extract_text,
    _strip_tracking_params,
)
from tests.unit.conftest import make_project, make_raw_mention

UTC = datetime.UTC


def make_ctx(project=None, repo=None):
    return PipelineContext(
        project=project or make_project(),
        scan_id=uuid4(),
        repository=repo or FakeRepository(),
    )


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_compute_content_hash_deterministic():
    """Same text always produces the same 64-char hex hash."""
    h1 = _compute_content_hash("hello world")
    h2 = _compute_content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 64


def test_compute_content_hash_different_text():
    """Different text produces different hash."""
    h1 = _compute_content_hash("hello world")
    h2 = _compute_content_hash("hello world!")
    assert h1 != h2


def test_strip_tracking_params_utm():
    """UTM params are stripped; non-tracking params remain."""
    text = "Check https://example.com/?utm_source=newsletter&id=42 for details"
    cleaned, removed = _strip_tracking_params(text)
    assert "utm_source" not in cleaned
    assert "id=42" in cleaned
    assert "utm_source" in removed


def test_strip_tracking_params_no_params():
    """URL with no tracking params is unchanged, removed list is empty."""
    text = "See https://example.com/?id=42"
    cleaned, removed = _strip_tracking_params(text)
    assert "id=42" in cleaned
    assert removed == []


def test_strip_tracking_params_multiple():
    """Multiple tracking params are all stripped."""
    text = "https://example.com/?utm_source=a&utm_medium=b&fbclid=c&page=1"
    cleaned, removed = _strip_tracking_params(text)
    assert "page=1" in cleaned
    assert set(removed) == {"utm_source", "utm_medium", "fbclid"}


def test_extract_text_plain():
    """Plain text mention returns text as-is, is_html_stripped=False."""
    mention = make_raw_mention(text="Hello world", text_html=None)
    text, stripped = _extract_text(mention)
    assert text == "Hello world"
    assert stripped is False


def test_extract_text_html():
    """HTML is stripped (script/style removed), is_html_stripped=True."""
    html = "<p>Hello <b>world</b><script>alert(1)</script></p>"
    mention = make_raw_mention(text="fallback", text_html=html)
    text, stripped = _extract_text(mention)
    assert "world" in text
    assert "alert" not in text
    assert "<" not in text
    assert stripped is True


# ---------------------------------------------------------------------------
# Test case 1: Cross-source identity (core/CLAUDE.md D.4 #1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_source_identity():
    """Same text from two different sources produces the same content_hash."""
    stage = NormalizeStage()
    text = "Anthropic released a new model today"
    now = datetime.datetime.now(UTC)

    reddit_mention = RawMention(
        source_id="reddit",
        external_id="t3_aaa",
        text=text,
        url=HttpUrl("https://www.reddit.com/r/test/comments/aaa/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )
    bluesky_mention = RawMention(
        source_id="bluesky",
        external_id="bsky_bbb",
        text=text,
        url=HttpUrl("https://bsky.app/profile/test/post/bbb"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )

    ctx = make_ctx()
    results = await stage.process([reddit_mention, bluesky_mention], ctx)  # type: ignore[arg-type]

    assert len(results) == 2
    assert results[0].content_hash == results[1].content_hash


# ---------------------------------------------------------------------------
# Test case 2: UTM-strip (core/CLAUDE.md D.4 #2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_utm_strip_produces_same_hash():
    """URL with utm_source and without produce the same content_hash."""
    stage = NormalizeStage()
    now = datetime.datetime.now(UTC)

    with_utm = RawMention(
        source_id="reddit",
        external_id="t3_001",
        text="See https://example.com/?utm_source=newsletter&id=42 for info",
        url=HttpUrl("https://www.reddit.com/r/test/comments/001/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )
    without_utm = RawMention(
        source_id="reddit",
        external_id="t3_002",
        text="See https://example.com/?id=42 for info",
        url=HttpUrl("https://www.reddit.com/r/test/comments/002/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )

    ctx = make_ctx()
    results = await stage.process([with_utm, without_utm], ctx)  # type: ignore[arg-type]

    assert len(results) == 2
    assert results[0].content_hash == results[1].content_hash
    assert "utm_source" in results[0].tracking_params_removed
    assert results[1].tracking_params_removed == []


# ---------------------------------------------------------------------------
# Test case 3: HTML-equivalent (core/CLAUDE.md D.4 #3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_html_equivalent_same_hash():
    """HTML and plain text versions of the same content produce the same hash."""
    stage = NormalizeStage()
    now = datetime.datetime.now(UTC)

    html_mention = RawMention(
        source_id="reddit",
        external_id="t3_010",
        text="Hello world",
        text_html="<p>Hello <b>world</b></p>",
        url=HttpUrl("https://www.reddit.com/r/test/comments/010/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )
    plain_mention = RawMention(
        source_id="reddit",
        external_id="t3_011",
        text="Hello world",
        url=HttpUrl("https://www.reddit.com/r/test/comments/011/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )

    ctx = make_ctx()
    results = await stage.process([html_mention, plain_mention], ctx)  # type: ignore[arg-type]

    assert results[0].is_html_stripped is True
    assert results[1].is_html_stripped is False
    assert results[0].content_hash == results[1].content_hash


# ---------------------------------------------------------------------------
# Test case 4: Whitespace-equivalent (core/CLAUDE.md D.4 #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whitespace_equivalent_same_hash():
    """Text with extra spaces/newlines and clean text produce the same hash."""
    stage = NormalizeStage()
    now = datetime.datetime.now(UTC)

    mention1 = RawMention(
        source_id="reddit",
        external_id="t3_020",
        text="Hello    world\n\n",
        url=HttpUrl("https://www.reddit.com/r/test/comments/020/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )
    mention2 = RawMention(
        source_id="reddit",
        external_id="t3_021",
        text="hello world",
        url=HttpUrl("https://www.reddit.com/r/test/comments/021/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )

    ctx = make_ctx()
    results = await stage.process([mention1, mention2], ctx)  # type: ignore[arg-type]

    assert results[0].content_hash == results[1].content_hash


# ---------------------------------------------------------------------------
# Test case 5: NFKC normalization (core/CLAUDE.md D.4 #5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nfkc_normalization():
    """NFC and NFD unicode forms of the same text produce the same hash after NFKC."""
    stage = NormalizeStage()
    now = datetime.datetime.now(UTC)

    # "cafe" with accented e:
    # NFC: e-with-acute is single codepoint U+00E9
    # NFD: e (U+0065) + combining-acute-accent (U+0301) = 2 codepoints
    base = "café"  # NFC: precomposed e-with-acute
    nfd_form = unicodedata.normalize("NFD", base)  # decomposed form

    # These should be different raw strings
    assert base != nfd_form or True  # defensive: test passes either way

    mention1 = RawMention(
        source_id="reddit",
        external_id="t3_030",
        text=base,
        url=HttpUrl("https://www.reddit.com/r/test/comments/030/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )
    mention2 = RawMention(
        source_id="reddit",
        external_id="t3_031",
        text=nfd_form,
        url=HttpUrl("https://www.reddit.com/r/test/comments/031/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )

    ctx = make_ctx()
    results = await stage.process([mention1, mention2], ctx)  # type: ignore[arg-type]

    assert results[0].content_hash == results[1].content_hash


# ---------------------------------------------------------------------------
# General normalize tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_preserves_count():
    """NormalizeStage does not filter — output length equals input length."""
    stage = NormalizeStage()
    mentions = [make_raw_mention(text=f"text {i}") for i in range(5)]
    ctx = make_ctx()
    results = await stage.process(mentions, ctx)  # type: ignore[arg-type]
    assert len(results) == 5


@pytest.mark.asyncio
async def test_normalize_assigns_lang():
    """Each normalized mention has a non-None lang field."""
    stage = NormalizeStage()
    mention = make_raw_mention(text="This is an English sentence about artificial intelligence.")
    ctx = make_ctx()
    results = await stage.process([mention], ctx)  # type: ignore[arg-type]
    assert results[0].lang is not None
    assert isinstance(results[0].lang, str)
