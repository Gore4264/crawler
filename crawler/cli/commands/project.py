"""CLI commands: project create / list / show / delete.

Section B.1-B.4 of cli/CLAUDE.md.
"""
from __future__ import annotations

from typing import Annotated

import structlog
import typer

import crawler.api_core.projects as projects_api
import crawler.api_core.signals as signals_api
from crawler.api_core.exceptions import ProjectAlreadyExistsError, ProjectNotFoundError
from crawler.cli._context import AppContext
from crawler.cli.formatters import (
    print_error,
    print_json,
    print_key_value,
    print_success,
    print_table,
)

logger = structlog.get_logger(__name__)

app = typer.Typer(name="project", help="Manage monitoring projects.", no_args_is_help=True)


# ---------------------------------------------------------------------------
# project create
# ---------------------------------------------------------------------------


@app.command("create")
def project_create(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Option("--name", "-n", help="Project slug [a-z0-9_-]+")] = None,
    keywords: Annotated[list[str], typer.Option("--keywords", "-k", help="Keywords (repeatable)")] = [],  # noqa: B006
    excluded: Annotated[list[str], typer.Option("--excluded", "-e", help="Excluded keywords")] = [],  # noqa: B006
    threshold: Annotated[float, typer.Option("--threshold", "-t", min=0.0, max=1.0)] = 0.7,
    format: Annotated[str, typer.Option("--format", help="table|json")] = "table",
) -> None:
    """Create a new monitoring project."""
    from crawler.cli.main import run_async

    app_ctx: AppContext = ctx.obj
    run_async(_project_create_async(app_ctx, name, list(keywords), list(excluded), threshold, format))


async def _project_create_async(
    app_ctx: AppContext,
    name: str | None,
    keywords: list[str],
    excluded: list[str],
    threshold: float,
    format: str,
) -> None:
    if not keywords:
        print_error("at least one --keywords required")
        raise typer.Exit(code=1)

    await app_ctx.connect()
    try:
        project = await projects_api.create_project(
            app_ctx.repository,
            name=name,
            keywords=keywords,
            excluded=excluded,
            threshold=threshold,
        )
        if format == "json":
            print_json(project.model_dump(mode="json"))
        else:
            print_success(f"Created project: {project.id}")
            _print_project_table(project)
    except ValueError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e
    except ProjectAlreadyExistsError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("unexpected_error", error=str(e))
        print_error(f"unexpected error: {type(e).__name__}: {e}")
        raise typer.Exit(code=2) from e
    finally:
        await app_ctx.disconnect()


# ---------------------------------------------------------------------------
# project list
# ---------------------------------------------------------------------------


@app.command("list")
def project_list(
    ctx: typer.Context,
    active_only: Annotated[bool, typer.Option("--active-only/--all")] = True,
    format: Annotated[str, typer.Option("--format", help="table|json")] = "table",
) -> None:
    """List all projects."""
    from crawler.cli.main import run_async

    app_ctx: AppContext = ctx.obj
    run_async(_project_list_async(app_ctx, active_only, format))


async def _project_list_async(
    app_ctx: AppContext,
    active_only: bool,
    format: str,
) -> None:
    await app_ctx.connect()
    try:
        projects = await projects_api.list_projects(app_ctx.repository, active_only=active_only)
        if not projects:
            typer.echo(
                "No projects found. Create one with: "
                "crawler project create --name=<slug> --keywords=<kw>"
            )
            return

        if format == "json":
            print_json([p.model_dump(mode="json") for p in projects])
        else:
            rows = []
            for p in projects:
                count = await signals_api.count_signals(app_ctx.repository, p.id)
                rows.append([p.id, p.name, str(count)])
            print_table(
                headers=["id", "name", "signals"],
                rows=rows,
                title="Projects",
            )
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("unexpected_error", error=str(e))
        print_error(f"unexpected error: {type(e).__name__}: {e}")
        raise typer.Exit(code=2) from e
    finally:
        await app_ctx.disconnect()


# ---------------------------------------------------------------------------
# project show
# ---------------------------------------------------------------------------


@app.command("show")
def project_show(
    ctx: typer.Context,
    project_id: Annotated[str, typer.Argument(help="Project id")],
    format: Annotated[str, typer.Option("--format", help="table|json")] = "table",
) -> None:
    """Show detailed project info."""
    from crawler.cli.main import run_async

    app_ctx: AppContext = ctx.obj
    run_async(_project_show_async(app_ctx, project_id, format))


async def _project_show_async(
    app_ctx: AppContext,
    project_id: str,
    format: str,
) -> None:
    await app_ctx.connect()
    try:
        project = await projects_api.get_project(app_ctx.repository, project_id)
        if format == "json":
            print_json(project.model_dump(mode="json"))
        else:
            _print_project_table(project)
            signals_count = await signals_api.count_signals(app_ctx.repository, project_id)
            print_key_value(
                [("signals total:", str(signals_count))],
                title="Statistics",
            )
    except ProjectNotFoundError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("unexpected_error", error=str(e))
        print_error(f"unexpected error: {type(e).__name__}: {e}")
        raise typer.Exit(code=2) from e
    finally:
        await app_ctx.disconnect()


# ---------------------------------------------------------------------------
# project delete
# ---------------------------------------------------------------------------


@app.command("delete")
def project_delete(
    ctx: typer.Context,
    project_id: Annotated[str, typer.Argument(help="Project id")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete a project and all its signals."""
    from crawler.cli.main import run_async

    app_ctx: AppContext = ctx.obj
    run_async(_project_delete_async(app_ctx, project_id, force))


async def _project_delete_async(
    app_ctx: AppContext,
    project_id: str,
    force: bool,
) -> None:
    await app_ctx.connect()
    try:
        await projects_api.get_project(app_ctx.repository, project_id)
        signals_count = await signals_api.count_signals(app_ctx.repository, project_id)

        if not force:
            typer.echo(
                f"This will delete project '{project_id}' "
                f"and all its signals ({signals_count} signals).\n"
                "Mentions will be preserved (shared global cache)."
            )
            confirmed = typer.prompt("Are you sure? [y/N]", default="N")
            if confirmed.strip().lower() != "y":
                typer.echo("Cancelled.")
                return

        await projects_api.delete_project(app_ctx.repository, project_id, cascade=True)
        print_success(f"Deleted project '{project_id}'.")
    except ProjectNotFoundError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("unexpected_error", error=str(e))
        print_error(f"unexpected error: {type(e).__name__}: {e}")
        raise typer.Exit(code=2) from e
    finally:
        await app_ctx.disconnect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_project_table(project: object) -> None:
    """Print project fields as a key-value table."""
    from crawler.core.models import Project

    if not isinstance(project, Project):
        return

    keywords_str = ", ".join(project.queries[0].keywords) if project.queries else "(none)"
    excluded_str = (
        ", ".join(project.queries[0].excluded_keywords) if project.queries else "(none)"
    ) or "(none)"
    sources_str = ", ".join(project.sources)
    pipeline_str = ", ".join(str(s) for s in project.pipeline)

    print_key_value(
        [
            ("id:", project.id),
            ("name:", project.name),
            ("keywords:", keywords_str),
            ("excluded:", excluded_str),
            ("threshold:", str(project.threshold)),
            ("sources:", sources_str),
            ("budget:", f"${project.budget.monthly_usd}/month"),
            ("schedule:", project.schedule_default),
            ("pipeline:", pipeline_str),
        ],
        title=f"Project: {project.id}",
    )
