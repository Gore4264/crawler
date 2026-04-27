"""Unit tests for api_core/projects.py.

All tests run against FakeRepository — no real Postgres required.
"""
from __future__ import annotations

import pytest

from crawler.api_core import projects as projects_api
from crawler.api_core.exceptions import ProjectAlreadyExistsError, ProjectNotFoundError
from crawler.processing._fakes import FakeRepository

# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_project_returns_project():
    repo = FakeRepository()
    project = await projects_api.create_project(
        repo,
        name="test-proj",
        keywords=["anthropic"],
    )
    assert project.id == "test-proj"
    assert project.name == "test-proj"
    assert project.queries[0].keywords == ["anthropic"]
    assert project.sources == ["reddit"]


@pytest.mark.asyncio
async def test_create_project_auto_name():
    """If name is None, an auto-generated slug is produced."""
    repo = FakeRepository()
    project = await projects_api.create_project(
        repo,
        name=None,
        keywords=["openai"],
    )
    assert project.id.startswith("project-")
    assert "openai" in project.queries[0].keywords


@pytest.mark.asyncio
async def test_create_project_invalid_slug_raises():
    repo = FakeRepository()
    with pytest.raises(ValueError, match="invalid project name"):
        await projects_api.create_project(
            repo,
            name="INVALID NAME!",
            keywords=["kw"],
        )


@pytest.mark.asyncio
async def test_create_project_duplicate_raises():
    repo = FakeRepository()
    await projects_api.create_project(repo, name="my-proj", keywords=["kw"])
    with pytest.raises(ProjectAlreadyExistsError):
        await projects_api.create_project(repo, name="my-proj", keywords=["kw2"])


@pytest.mark.asyncio
async def test_create_project_no_keywords_raises():
    repo = FakeRepository()
    with pytest.raises((ValueError, Exception)):
        await projects_api.create_project(repo, name="empty", keywords=[])


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_projects_empty():
    repo = FakeRepository()
    projects = await projects_api.list_projects(repo)
    assert projects == []


@pytest.mark.asyncio
async def test_list_projects_returns_all():
    repo = FakeRepository()
    await projects_api.create_project(repo, name="proj-a", keywords=["a"])
    await projects_api.create_project(repo, name="proj-b", keywords=["b"])
    projects = await projects_api.list_projects(repo)
    ids = {p.id for p in projects}
    assert ids == {"proj-a", "proj-b"}


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_found():
    repo = FakeRepository()
    await projects_api.create_project(repo, name="look-me-up", keywords=["kw"])
    project = await projects_api.get_project(repo, "look-me-up")
    assert project.id == "look-me-up"


@pytest.mark.asyncio
async def test_get_project_not_found_raises():
    repo = FakeRepository()
    with pytest.raises(ProjectNotFoundError):
        await projects_api.get_project(repo, "does-not-exist")


# ---------------------------------------------------------------------------
# delete_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_project_removes_it():
    repo = FakeRepository()
    await projects_api.create_project(repo, name="to-delete", keywords=["kw"])
    await projects_api.delete_project(repo, "to-delete")
    with pytest.raises(ProjectNotFoundError):
        await projects_api.get_project(repo, "to-delete")


@pytest.mark.asyncio
async def test_delete_project_not_found_raises():
    repo = FakeRepository()
    with pytest.raises(ProjectNotFoundError):
        await projects_api.delete_project(repo, "ghost")
