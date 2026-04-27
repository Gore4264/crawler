"""CLI unit tests for `scan` command.

Uses typer.testing.CliRunner + FakeRepository + patched source_factory.
No real Postgres or Reddit connection.
"""
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from crawler.cli.main import app
from crawler.processing._fakes import FakeRepository
from tests.unit.conftest import make_project

runner = CliRunner()
_FAKE_DSN = "postgresql://fake:fake@localhost:5432/fake"


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


def test_scan_missing_project_flag(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["scan"])

    assert result.exit_code == 1
    assert "--project" in result.output or "required" in result.output.lower()


def test_scan_project_not_found(monkeypatch):
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    ctx = _patched_ctx(repo)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["scan", "--project=ghost"])

    assert result.exit_code == 1


def test_scan_success_with_stub_source(monkeypatch):
    """Scan with a real FakeRepository + stub source returns exit 0."""
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    repo = FakeRepository()
    project = make_project(project_id="scan-proj", keywords=["anthropic"])

    import asyncio

    asyncio.run(repo.create_project(project))

    # Patch run_scan to avoid real Reddit and pipeline
    from decimal import Decimal

    from crawler.api_core.scanning import ScanResult

    fake_result = ScanResult(
        project_id="scan-proj",
        source_id="reddit",
        query_name="main_topic",
        mentions_fetched=5,
        mentions_inserted=5,
        duplicates=0,
        signals_created=3,
        cost_usd=Decimal("0"),
        duration_seconds=0.1,
        status="ok",
    )

    ctx = _patched_ctx(repo)

    async def _fake_run_scan(*args, **kwargs):
        return [fake_result]

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx), \
         patch("crawler.api_core.scanning.run_scan", side_effect=_fake_run_scan):
        result = runner.invoke(app, ["scan", "--project=scan-proj"])

    assert result.exit_code == 0
    assert "Scan complete" in result.output or "scan" in result.output.lower()


def test_scan_reddit_creds_missing(monkeypatch):
    """RedditCredentialsMissingError → exit 1."""
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)

    repo = FakeRepository()
    project = make_project(project_id="cred-proj", keywords=["kw"])

    import asyncio
    asyncio.run(repo.create_project(project))

    ctx = _patched_ctx(repo)

    from crawler.api_core.exceptions import RedditCredentialsMissingError

    async def _raise_creds(*args, **kwargs):
        raise RedditCredentialsMissingError("REDDIT_CLIENT_ID")

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx), \
         patch("crawler.api_core.scanning.run_scan", side_effect=_raise_creds):
        result = runner.invoke(app, ["scan", "--project=cred-proj"])

    assert result.exit_code == 1
