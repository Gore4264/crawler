"""CLI unit tests for `signals` and `signal show` commands.

Uses typer.testing.CliRunner + FakeRepository.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
from decimal import Decimal
from uuid import uuid4

from typer.testing import CliRunner

from crawler.cli.main import app
from crawler.core.models import NormalizedMention, PipelineTraceEntry, Signal
from crawler.processing._fakes import FakeRepository

runner = CliRunner()
_FAKE_DSN = "postgresql://fake:fake@localhost:5432/fake"
UTC = datetime.UTC


def _patched_ctx(repo: FakeRepository):
    from crawler.cli._context import AppContext

    ctx = AppContext(database_dsn=_FAKE_DSN)
    ctx._repository = repo

    async def _noop():
        pass

    ctx.connect = _noop  # type: ignore[method-assign]
    ctx.disconnect = _noop  # type: ignore[method-assign]
    return ctx


def _make_mention(text: str = "anthropic released claude") -> NormalizedMention:
    from pydantic import HttpUrl

    now = datetime.datetime.now(UTC)
    text_clean = text.lower()
    return NormalizedMention(
        source_id="reddit",
        external_id=f"t3_{uuid4().hex[:8]}",
        text=text,
        text_html=None,
        url=HttpUrl("https://reddit.com/r/test/comments/abc/"),
        published_at=now,
        discovered_at=now,
        fetched_at=now,
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
        relevance_score=0.9,
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
# signals (list)
# ---------------------------------------------------------------------------


def test_signals_missing_project_flag(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    from unittest.mock import patch

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["signals"])

    assert result.exit_code == 1


def test_signals_empty_project(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    from unittest.mock import patch

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["signals", "--project=no-signals"])

    assert result.exit_code == 0
    assert "No signals" in result.output


def test_signals_shows_table(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    sig = _make_signal("my-proj")
    asyncio.run(repo.insert_signals([sig]))
    ctx = _patched_ctx(repo)

    from unittest.mock import patch

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["signals", "--project=my-proj"])

    assert result.exit_code == 0
    assert "my-proj" in result.output or "other" in result.output


def test_signals_json_format(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    sig = _make_signal("json-proj")
    asyncio.run(repo.insert_signals([sig]))
    ctx = _patched_ctx(repo)

    from unittest.mock import patch

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["signals", "--project=json-proj", "--format=json"])

    assert result.exit_code == 0
    assert "json-proj" in result.output


# ---------------------------------------------------------------------------
# signal show
# ---------------------------------------------------------------------------


def test_signal_show_found(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    mention = _make_mention("hello anthropic world")
    sig = _make_signal("show-proj", mention_id=mention.id)
    asyncio.run(repo.bulk_upsert_mentions_with_dedup([mention]))
    asyncio.run(repo.insert_signals([sig]))
    ctx = _patched_ctx(repo)

    from unittest.mock import patch

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["signal", str(sig.id)])

    assert result.exit_code == 0
    assert str(sig.id) in result.output or "show-proj" in result.output


def test_signal_show_not_found(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    from unittest.mock import patch

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["signal", str(uuid4())])

    assert result.exit_code == 1


def test_signal_show_invalid_uuid(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    from unittest.mock import patch

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["signal", "not-a-uuid"])

    assert result.exit_code == 1
