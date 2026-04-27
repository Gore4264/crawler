"""Shared fixtures for unit tests."""
from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import HttpUrl

from crawler.core.models import (
    BudgetConfig,
    NotificationConfig,
    Project,
    RawMention,
    TopicQuery,
)
from crawler.processing._fakes import FakeRepository

UTC = datetime.UTC


def make_raw_mention(
    text: str = "Hello world from tests",
    source_id: str = "reddit",
    external_id: str | None = None,
    text_html: str | None = None,
    url: str = "https://www.reddit.com/r/test/comments/abc123/",
) -> RawMention:
    """Factory for RawMention test fixtures."""
    now = datetime.datetime.now(UTC)
    return RawMention(
        source_id=source_id,
        external_id=external_id or f"t3_{uuid4().hex[:8]}",
        text=text,
        text_html=text_html,
        url=HttpUrl(url),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
    )


def make_project(
    project_id: str = "test_project",
    keywords: list[str] | None = None,
    excluded_keywords: list[str] | None = None,
) -> Project:
    """Factory for Project test fixtures."""
    kw = ["anthropic", "claude"] if keywords is None else keywords
    excl = [] if excluded_keywords is None else excluded_keywords
    return Project(
        id=project_id,
        name="Test Project",
        queries=[
            TopicQuery(
                name="main_topic",
                keywords=kw,
                excluded_keywords=excl,
            )
        ],
        sources=["reddit"],
        notifications=[
            NotificationConfig(channel="telegram", target="123456789")
        ],
        budget=BudgetConfig(monthly_usd=Decimal("10.00")),
        pipeline=["normalize", "dedup", "keyword_filter", "decide"],
        schedule_default="0 * * * *",
    )


@pytest.fixture
def fake_repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def test_project() -> Project:
    return make_project()


@pytest.fixture
def raw_mention() -> RawMention:
    return make_raw_mention()
