"""CLI commands: signals (list) and signal show.

Sections B.6-B.7 of cli/CLAUDE.md.
"""
from __future__ import annotations

import datetime
from typing import Annotated
from uuid import UUID

import structlog
import typer

import crawler.api_core.signals as signals_api
from crawler.cli._context import AppContext
from crawler.cli.formatters import (
    print_error,
    print_json,
    print_jsonl,
    print_key_value,
    print_table,
)

logger = structlog.get_logger(__name__)

_UTC = datetime.UTC


def _parse_since(value: str | None) -> datetime.datetime | None:
    """Parse --since value: ISO datetime, date, or shortcut like '24h'/'7d'."""
    if value is None:
        return None
    if value.endswith("h"):
        hours = int(value[:-1])
        return datetime.datetime.now(_UTC) - datetime.timedelta(hours=hours)
    if value.endswith("d"):
        days = int(value[:-1])
        return datetime.datetime.now(_UTC) - datetime.timedelta(days=days)
    # Try ISO parse
    try:
        dt = datetime.datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        return dt
    except ValueError:
        raise typer.BadParameter(f"Cannot parse --since value: '{value}'")


def signals_command(
    ctx: typer.Context,
    project_id: Annotated[str, typer.Option("--project", "-p", help="Project id")] = "",
    since: Annotated[str | None, typer.Option("--since", "-s", help="Since: ISO datetime or 24h/7d")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max signals")] = 50,
    query: Annotated[str | None, typer.Option("--query", "-q", help="Text filter (ILIKE)")] = None,
    format: Annotated[str, typer.Option("--format", help="table|json|jsonl")] = "table",
) -> None:
    """List signals for a project."""
    from crawler.cli.main import run_async

    if not project_id:
        print_error("--project is required")
        raise typer.Exit(code=1)

    app_ctx: AppContext = ctx.obj
    since_dt = _parse_since(since)
    run_async(_signals_async(app_ctx, project_id, since_dt, limit, query, format))


async def _signals_async(
    app_ctx: AppContext,
    project_id: str,
    since: datetime.datetime | None,
    limit: int,
    query: str | None,
    format: str,
) -> None:
    await app_ctx.connect()
    try:
        sigs = await signals_api.search_signals(
            app_ctx.repository,
            project_id,
            since=since,
            limit=limit,
            text_query=query,
        )

        if not sigs:
            typer.echo(
                f"No signals found for project '{project_id}'. "
                f"Run: crawler scan --project={project_id}"
            )
            return

        if format == "json":
            print_json([s.model_dump(mode="json") for s in sigs])
        elif format == "jsonl":
            print_jsonl([s.model_dump(mode="json") for s in sigs])
        else:
            rows = []
            for s in sigs:
                created = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
                rows.append([
                    created,
                    f"{s.relevance_score:.2f}",
                    s.intent,
                    str(s.id)[:8] + "...",
                ])
            print_table(
                headers=["created_at", "score", "intent", "signal_id"],
                rows=rows,
                title=f"Signals for '{project_id}' (last {limit})",
            )
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("unexpected_error", error=str(e))
        print_error(f"unexpected error: {type(e).__name__}: {e}")
        raise typer.Exit(code=2) from e
    finally:
        await app_ctx.disconnect()


def signal_show_command(
    ctx: typer.Context,
    signal_id: Annotated[str, typer.Argument(help="Signal UUID")],
) -> None:
    """Show detailed info for a single signal."""
    from crawler.cli.main import run_async

    app_ctx: AppContext = ctx.obj
    try:
        uid = UUID(signal_id)
    except ValueError:
        print_error(f"invalid UUID: '{signal_id}'")
        raise typer.Exit(code=1)

    run_async(_signal_show_async(app_ctx, uid))


async def _signal_show_async(app_ctx: AppContext, signal_id: UUID) -> None:
    await app_ctx.connect()
    try:
        result = await signals_api.get_signal_with_mention(app_ctx.repository, signal_id)
        if result is None:
            print_error(f"signal '{signal_id}' not found")
            raise typer.Exit(code=1)

        signal, mention = result

        print_key_value(
            [
                ("project:", signal.project_id),
                ("matched_query:", signal.matched_query),
                ("relevance:", f"{signal.relevance_score:.2f}"),
                ("is_spam:", str(signal.is_spam)),
                ("intent:", signal.intent),
                ("sentiment:", signal.sentiment),
                ("cost:", f"${signal.cost_usd:.6f}"),
                ("created_at:", signal.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")),
            ],
            title=f"Signal {signal_id}",
        )

        print_key_value(
            [
                ("source:", mention.source_id),
                ("url:", str(mention.url)),
                ("published_at:", mention.published_at.strftime("%Y-%m-%d %H:%M:%S UTC")),
                ("lang:", mention.lang),
                ("text:", mention.text_clean[:200] + ("..." if len(mention.text_clean) > 200 else "")),
            ],
            title="Mention",
        )

        if signal.pipeline_trace:
            trace_rows = [
                [
                    t.stage_name,
                    f"{t.duration_ms}ms",
                    f"{t.items_in}→{t.items_out}",
                    f"${t.cost_usd:.6f}",
                ]
                for t in signal.pipeline_trace
            ]
            print_table(
                headers=["stage", "duration", "items", "cost"],
                rows=trace_rows,
                title="Pipeline trace",
            )

    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("unexpected_error", error=str(e))
        print_error(f"unexpected error: {type(e).__name__}: {e}")
        raise typer.Exit(code=2) from e
    finally:
        await app_ctx.disconnect()
