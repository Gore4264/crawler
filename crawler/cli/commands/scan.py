"""CLI command: scan --project=<id>.

Section B.5 of cli/CLAUDE.md.
"""
from __future__ import annotations

from typing import Annotated

import structlog
import typer

import crawler.api_core.scanning as scanning_api
from crawler.api_core.exceptions import ProjectNotFoundError, RedditCredentialsMissingError
from crawler.cli._context import AppContext
from crawler.cli.formatters import print_error, print_json, print_key_value, print_success

logger = structlog.get_logger(__name__)


def scan_command(
    ctx: typer.Context,
    project_id: Annotated[str, typer.Option("--project", "-p", help="Project id")] = "",
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max mentions per query")] = 100,
    format: Annotated[str, typer.Option("--format", help="table|json")] = "table",
) -> None:
    """Scan sources for a project and store signals."""
    from crawler.cli.main import run_async

    if not project_id:
        print_error("--project is required")
        raise typer.Exit(code=1)

    app_ctx: AppContext = ctx.obj
    run_async(_scan_async(app_ctx, project_id, limit, format))


async def _scan_async(
    app_ctx: AppContext,
    project_id: str,
    limit: int,
    format: str,
) -> None:
    await app_ctx.connect()
    try:
        typer.echo(f"Scanning project '{project_id}' via reddit...")

        def _progress(msg: str) -> None:
            typer.echo(f"  {msg}")

        results = await scanning_api.run_scan(
            app_ctx.repository,
            project_id,
            limit=limit,
            progress_callback=_progress,
        )

        if format == "json":
            import dataclasses
            print_json([dataclasses.asdict(r) for r in results])
        else:
            print_success("Scan complete")
            for r in results:
                print_key_value(
                    [
                        ("Project:", r.project_id),
                        ("Source:", r.source_id),
                        ("Query:", r.query_name),
                        ("Mentions total:", str(r.mentions_fetched)),
                        ("New (inserted):", str(r.mentions_inserted)),
                        ("Duplicates:", str(r.duplicates)),
                        ("Signals:", str(r.signals_created)),
                        ("Cost:", f"${r.cost_usd:.6f}"),
                        ("Duration:", f"{r.duration_seconds:.1f}s"),
                    ]
                )
    except ProjectNotFoundError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e
    except RedditCredentialsMissingError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e
    except KeyboardInterrupt:
        typer.echo("\nInterrupted by user")
        raise typer.Exit(code=130)
    except typer.Exit:
        raise
    except Exception as e:
        logger.exception("unexpected_error", error=str(e))
        print_error(f"unexpected error: {type(e).__name__}: {e}")
        raise typer.Exit(code=2) from e
    finally:
        await app_ctx.disconnect()
