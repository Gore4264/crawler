"""Output formatters for CLI commands.

Rule: stdout for results (table/json/jsonl), stderr for errors and logs.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()  # stdout
err_console = Console(stderr=True)  # stderr


def print_table(headers: list[str], rows: list[list[str]], title: str = "") -> None:
    """Render a Rich Table to stdout."""
    table = Table(title=title, show_header=True, header_style="bold")
    for h in headers:
        table.add_column(h)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def print_key_value(pairs: list[tuple[str, str]], title: str = "") -> None:
    """Print key-value pairs as a simple two-column table."""
    if title:
        console.print(f"\n[bold]{title}[/bold]")
        console.rule()
    for key, value in pairs:
        console.print(f"{key:<20} {value}")


def print_json(data: Any) -> None:
    """Print JSON-serialised data to stdout via Rich."""
    console.print_json(json.dumps(data, default=str))


def print_jsonl(items: list[Any]) -> None:
    """Print one JSON object per line to stdout."""
    for item in items:
        sys.stdout.write(json.dumps(item, default=str) + "\n")
    sys.stdout.flush()


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    err_console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print a success message to stdout."""
    console.print(f"[green]{message}[/green]")


def print_warning(message: str) -> None:
    """Print a warning message to stdout."""
    console.print(f"[yellow]Warning:[/yellow] {message}")
