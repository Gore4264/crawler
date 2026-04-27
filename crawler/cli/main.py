"""CLI entry-point for crawler.

`crawler` command registered in pyproject.toml [project.scripts].
Global flags: --verbose / --format.

Async commands run via run_async() helper that wraps asyncio.run().
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Coroutine
from typing import Annotated, Any, TypeVar

import structlog
import typer

from crawler.cli._context import AppContext

# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="crawler",
    help="Personal deep-monitoring crawler — sources to filtered signals.",
    no_args_is_help=True,
    add_completion=False,
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine from a sync CLI entry-point."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    """Configure structlog + stdlib logging.

    Logs always go to stderr (invariant from cli/CLAUDE.md).
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


# ---------------------------------------------------------------------------
# Global app callback (runs before every command)
# ---------------------------------------------------------------------------


@app.callback()
def _global_callback(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable DEBUG logging.")] = False,
    format: Annotated[str, typer.Option("--format", help="Output format: table|json|jsonl.")] = "table",
) -> None:
    """Crawler — personal deep-monitoring system."""
    _configure_logging(verbose)

    # Load .env if python-dotenv is available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    ctx.ensure_object(dict)
    ctx.obj = AppContext.from_env(verbose=verbose)
    # Store format globally so commands can use it
    ctx.meta["format"] = format


# ---------------------------------------------------------------------------
# Sub-command registration
# ---------------------------------------------------------------------------


def _register_commands() -> None:
    """Lazily register sub-commands to avoid circular import issues."""
    from crawler.cli.commands import project, scan, signals, usage

    app.add_typer(project.app, name="project")
    app.command(name="scan")(scan.scan_command)
    app.command(name="signals")(signals.signals_command)
    app.command(name="signal")(signals.signal_show_command)
    app.command(name="usage")(usage.usage_command)


_register_commands()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    app()
