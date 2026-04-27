"""Domain exceptions for api_core/ service layer.

These are raised by api_core functions and caught by CLI commands
(mapped to appropriate exit codes per cli/CLAUDE.md E.1).
"""
from __future__ import annotations


class CrawlerError(Exception):
    """Base class for all domain errors."""


class ProjectNotFoundError(CrawlerError):
    def __init__(self, project_id: str) -> None:
        super().__init__(f"project '{project_id}' not found")
        self.project_id = project_id


class ProjectAlreadyExistsError(CrawlerError):
    def __init__(self, project_id: str) -> None:
        super().__init__(f"project '{project_id}' already exists")
        self.project_id = project_id


class RedditCredentialsMissingError(CrawlerError):
    def __init__(self, var_name: str) -> None:
        super().__init__(
            f"{var_name} not set. "
            "Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT "
            "in environment or .env file."
        )
        self.var_name = var_name


class SourceUnavailableError(CrawlerError):
    """Raised when a source returns an error or is unreachable."""

    def __init__(self, source_id: str, message: str) -> None:
        super().__init__(f"source '{source_id}' unavailable: {message}")
        self.source_id = source_id


class DatabaseError(CrawlerError):
    """Raised on database connectivity failures."""
