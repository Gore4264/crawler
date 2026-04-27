"""CLI unit tests for `usage` command.

Uses typer.testing.CliRunner + FakeRepository.
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

from typer.testing import CliRunner

from crawler.cli.main import app
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_usage_missing_project_flag(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["usage"])

    assert result.exit_code == 1
    assert "--project" in result.output or "project" in result.output.lower()


def test_usage_no_data(monkeypatch):
    """No usage data in FakeRepository → informational message, exit 0."""
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["usage", "--project=test-proj"])

    assert result.exit_code == 0
    # Either "No usage data" or a table with $0 total is acceptable
    assert "test-proj" in result.output or "No usage" in result.output or "$" in result.output


def test_usage_with_since_flag(monkeypatch):
    """--since flag accepted without error."""
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["usage", "--project=test-proj", "--since=2026-04-01"])

    assert result.exit_code == 0


def test_usage_project_not_found(monkeypatch):
    """ProjectNotFoundError → exit 1."""
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    from crawler.api_core.exceptions import ProjectNotFoundError

    async def _raise(*args, **kwargs):
        raise ProjectNotFoundError("ghost-proj")

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx), \
         patch("crawler.api_core.signals.get_usage_summary", side_effect=_raise):
        result = runner.invoke(app, ["usage", "--project=ghost-proj"])

    assert result.exit_code == 1
