"""Unit tests for api_core/scanning.py.

Uses FakeRepository + a stub source_factory so no real Reddit connection is made.
"""
from __future__ import annotations

import datetime
from collections.abc import AsyncIterator

import pytest

from crawler.api_core import scanning as scanning_api
from crawler.api_core.exceptions import ProjectNotFoundError
from crawler.core.models import RawMention, SourceQuery
from crawler.processing._fakes import FakeRepository
from tests.unit.conftest import make_project, make_raw_mention

UTC = datetime.UTC


class _StubSource:
    """Stub ISource that returns a fixed list of RawMentions."""

    def __init__(self, mentions: list[RawMention]) -> None:
        self._mentions = mentions
        self.called_queries: list[SourceQuery] = []

    async def search(self, query: SourceQuery) -> AsyncIterator[RawMention]:
        self.called_queries.append(query)
        for m in self._mentions:
            yield m


def _make_factory(mentions: list[RawMention]):
    """Return a source_factory compatible with run_scan signature."""
    stub = _StubSource(mentions)

    def factory(project):  # noqa: ANN001
        return stub

    factory._stub = stub  # expose for assertions
    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_scan_project_not_found():
    repo = FakeRepository()
    with pytest.raises(ProjectNotFoundError):
        await scanning_api.run_scan(repo, "no-such-project", source_factory=_make_factory([]))


@pytest.mark.asyncio
async def test_run_scan_returns_result():
    repo = FakeRepository()
    project = make_project(project_id="my-proj", keywords=["anthropic"])
    await repo.create_project(project)

    mentions = [
        make_raw_mention("Anthropic released claude today for everyone"),
        make_raw_mention("Something unrelated about cooking"),
    ]
    factory = _make_factory(mentions)

    results = await scanning_api.run_scan(repo, "my-proj", source_factory=factory)

    assert len(results) == 1  # one query in make_project
    result = results[0]
    assert result.project_id == "my-proj"
    assert result.source_id == "reddit"
    assert result.mentions_fetched == 2
    assert result.signals_created >= 1  # "Anthropic" mention passes keyword filter


@pytest.mark.asyncio
async def test_run_scan_empty_source():
    repo = FakeRepository()
    project = make_project(project_id="empty-proj", keywords=["anthropic"])
    await repo.create_project(project)

    results = await scanning_api.run_scan(repo, "empty-proj", source_factory=_make_factory([]))

    assert len(results) == 1
    result = results[0]
    assert result.mentions_fetched == 0
    assert result.signals_created == 0
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_run_scan_progress_callback_called():
    repo = FakeRepository()
    project = make_project(project_id="prog-proj", keywords=["x"])
    await repo.create_project(project)

    messages: list[str] = []
    await scanning_api.run_scan(
        repo, "prog-proj",
        source_factory=_make_factory([]),
        progress_callback=lambda msg: messages.append(msg),
    )

    assert any("Fetching" in m for m in messages)
    assert any("pipeline" in m.lower() for m in messages)


@pytest.mark.asyncio
async def test_run_scan_dedup_across_runs():
    """Second scan with same text → 0 new mentions inserted (dedup), still records scan."""
    repo = FakeRepository()
    project = make_project(project_id="dedup-proj", keywords=["anthropic"])
    await repo.create_project(project)

    mention = make_raw_mention("Anthropic is leading AI safety research today")
    factory = _make_factory([mention])

    r1 = await scanning_api.run_scan(repo, "dedup-proj", source_factory=factory)
    r2 = await scanning_api.run_scan(repo, "dedup-proj", source_factory=factory)

    assert r1[0].mentions_inserted == 1
    assert r2[0].mentions_inserted == 0  # same hash → deduped
    assert r2[0].duplicates == 1
