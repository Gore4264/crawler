"""CLI command: usage --project=<id>.

Section B.8 of cli/CLAUDE.md.
"""
from __future__ import annotations

import datetime
from typing import Annotated

import structlog
import typer

import crawler.api_core.signals as signals_api
from crawler.api_core.exceptions import ProjectNotFoundError
from crawler.cli._context import AppContext
from crawler.cli.formatters import print_error, print_key_value, print_table

logger = structlog.get_logger(__name__)

_UTC = datetime.UTC


def _start_of_month() -> datetime.datetime:
    """Return the start of the current month (UTC)."""
    now = datetime.datetime.now(_UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_command(
    ctx: typer.Context,
    project_id: Annotated[str, typer.Option("--project", "-p", help="Project id")] = "",
    since: Annotated[str | None, typer.Option("--since", "-s", help="Start date (YYYY-MM-DD or ISO)")] = None,
) -> None:
    """Show usage/cost breakdown for a project."""
    from crawler.cli.main import run_async

    if not project_id:
        print_error("--project is required")
        raise typer.Exit(code=1)

    since_dt = _start_of_month()
    if since is not None:
        try:
            parsed = datetime.date.fromisoformat(since)
            since_dt = datetime.datetime(
                parsed.year, parsed.month, parsed.day, tzinfo=_UTC
            )
        except ValueError:
            try:
                since_dt = datetime.datetime.fromisoformat(since)
                if since_dt.tzinfo is None:
                    since_dt = since_dt.replace(tzinfo=_UTC)
            except ValueError:
                print_error(f"Cannot parse --since: '{since}'")
                raise typer.Exit(code=1)

    app_ctx: AppContext = ctx.obj
    run_async(_usage_async(app_ctx, project_id, since_dt))


async def _usage_async(
    app_ctx: AppContext,
    project_id: str,
    since: datetime.datetime,
) -> None:
    await app_ctx.connect()
    try:
        summary = await signals_api.get_usage_summary(
            app_ctx.repository,
            project_id,
            since=since,
        )

        since_str = since.strftime("%Y-%m-%d")

        if not summary.by_kind and summary.signals_count == 0:
            typer.echo(f"No usage data for project '{project_id}' since {since_str}")
            return

        # By kind table
        kind_rows = [
            [k, f"${v:.6f}"]
            for k, v in sorted(summary.by_kind.items())
        ]
        if kind_rows:
            print_table(
                headers=["Kind", "Cost"],
                rows=kind_rows,
                title=f"Usage for '{project_id}'  (since {since_str})",
            )

        # By source
        if summary.by_source:
            source_rows = [
                [s, f"${v:.6f}"]
                for s, v in sorted(summary.by_source.items())
            ]
            print_table(headers=["Source", "Cost"], rows=source_rows)

        cps = (
            f"${summary.cost_per_signal:.6f}  (KPI #3 target: < $0.50)"
            if summary.cost_per_signal is not None
            else "n/a"
        )
        print_key_value(
            [
                ("TOTAL:", f"${summary.total_usd:.6f}"),
                ("Signals this period:", str(summary.signals_count)),
                ("Cost per signal:", cps),
            ]
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
