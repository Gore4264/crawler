"""CLI unit tests for `project` sub-commands.

Uses typer.testing.CliRunner + a patched AppContext that injects FakeRepository.
No real Postgres or network calls.
"""
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from crawler.cli.main import app
from crawler.processing._fakes import FakeRepository

runner = CliRunner()

_FAKE_DSN = "postgresql://fake:fake@localhost:5432/fake"


def _patched_app_context(repo: FakeRepository):
    """Return an AppContext whose connect/disconnect are no-ops and repo is FakeRepository."""
    from crawler.cli._context import AppContext

    ctx = AppContext(database_dsn=_FAKE_DSN)
    ctx._repository = repo

    async def _noop_connect():
        pass

    async def _noop_disconnect():
        pass

    ctx.connect = _noop_connect  # type: ignore[method-assign]
    ctx.disconnect = _noop_disconnect  # type: ignore[method-assign]
    return ctx


# ---------------------------------------------------------------------------
# project create
# ---------------------------------------------------------------------------


def test_project_create_success(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)

    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(
            app,
            ["project", "create", "--name=my-proj", "--keywords=anthropic"],
        )

    assert result.exit_code == 0, result.output
    assert "my-proj" in result.output


def test_project_create_missing_keywords(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(
            app,
            ["project", "create", "--name=no-kw"],
        )

    assert result.exit_code == 1
    assert "keywords" in result.output.lower() or "keywords" in (result.stderr or "").lower()


def test_project_create_duplicate_error(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        # Create first time
        runner.invoke(app, ["project", "create", "--name=dup-proj", "--keywords=kw"])
        # Create second time → duplicate
        result = runner.invoke(app, ["project", "create", "--name=dup-proj", "--keywords=kw"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# project list
# ---------------------------------------------------------------------------


def test_project_list_empty(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 0
    assert "No projects" in result.output


def test_project_list_shows_projects(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        runner.invoke(app, ["project", "create", "--name=listed-proj", "--keywords=kw"])
        result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 0
    assert "listed-proj" in result.output


# ---------------------------------------------------------------------------
# project show
# ---------------------------------------------------------------------------


def test_project_show_found(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        runner.invoke(app, ["project", "create", "--name=show-me", "--keywords=kw"])
        result = runner.invoke(app, ["project", "show", "show-me"])

    assert result.exit_code == 0
    assert "show-me" in result.output


def test_project_show_not_found(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["project", "show", "ghost-project"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# project delete
# ---------------------------------------------------------------------------


def test_project_delete_force(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        runner.invoke(app, ["project", "create", "--name=del-proj", "--keywords=kw"])
        result = runner.invoke(app, ["project", "delete", "del-proj", "--force"])

    assert result.exit_code == 0
    assert "Deleted" in result.output


def test_project_delete_not_found(monkeypatch):
    repo = FakeRepository()
    ctx = _patched_app_context(repo)
    monkeypatch.setenv("CRAWLER_DATABASE_DSN", _FAKE_DSN)

    with patch("crawler.cli._context.AppContext.from_env", return_value=ctx):
        result = runner.invoke(app, ["project", "delete", "ghost", "--force"])

    assert result.exit_code == 1
