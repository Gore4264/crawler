"""Project CRUD service functions.

Thin wrappers over IRepository used by CLI commands and (E4) MCP tools.
No business logic beyond validation — all persistence through IRepository.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from crawler.api_core.exceptions import ProjectAlreadyExistsError, ProjectNotFoundError
from crawler.core.contracts import IRepository
from crawler.core.models import BudgetConfig, Project, TopicQuery

_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")

_E1_PIPELINE: list[str | dict[str, object]] = ["normalize", "dedup", "keyword_filter", "decide"]


def _generate_project_id() -> str:
    """Generate a timestamp-based project id."""
    return f"project-{int(datetime.now(UTC).timestamp())}"


async def create_project(
    repo: IRepository,
    *,
    name: str | None,
    keywords: list[str],
    excluded: list[str] | None = None,
    threshold: float = 0.7,
) -> Project:
    """
    Create a new project with Phase 0 defaults.

    1. Generate id if name is None.
    2. Validate slug regex.
    3. Check that no project with this id exists.
    4. Build Project with Phase 0 defaults.
    5. repo.create_project(project) → Project.
    """
    from decimal import Decimal

    project_id = name if name is not None else _generate_project_id()

    if not _SLUG_RE.match(project_id):
        raise ValueError(
            f"invalid project name '{project_id}': must match [a-z0-9_-]+"
        )

    if not keywords:
        raise ValueError("at least one --keywords required")

    if not (0.0 <= threshold <= 1.0):
        raise ValueError("threshold must be between 0.0 and 1.0")

    # Check for duplicate
    existing = await repo.get_project(project_id)
    if existing is not None:
        raise ProjectAlreadyExistsError(project_id)

    project = Project(
        id=project_id,
        name=project_id,
        queries=[
            TopicQuery(
                name=project_id.replace("-", "_"),
                keywords=list(keywords),
                excluded_keywords=list(excluded) if excluded else [],
            )
        ],
        sources=["reddit"],
        notifications=[],
        budget=BudgetConfig(monthly_usd=Decimal("10")),
        pipeline=_E1_PIPELINE,
        schedule_default="manual",
        threshold=threshold,
        settings={},
    )

    return await repo.create_project(project)


async def list_projects(
    repo: IRepository,
    *,
    active_only: bool = True,
) -> list[Project]:
    """Return list of projects from repository."""
    return await repo.list_projects(active_only=active_only)


async def get_project(
    repo: IRepository,
    project_id: str,
) -> Project:
    """
    Return project by id.
    Raises ProjectNotFoundError if not found.
    """
    project = await repo.get_project(project_id)
    if project is None:
        raise ProjectNotFoundError(project_id)
    return project


async def delete_project(
    repo: IRepository,
    project_id: str,
    *,
    cascade: bool = True,
) -> None:
    """
    Delete a project (and optionally its signals/scan_log/usage_log).

    1. Verify project exists (raises ProjectNotFoundError if not).
    2. Delegate to repo.delete_project(project_id, cascade=cascade).
    """
    # Verify existence first
    existing = await repo.get_project(project_id)
    if existing is None:
        raise ProjectNotFoundError(project_id)

    await repo.delete_project(project_id, cascade=cascade)
