"""SOURCE_REGISTRY — global registry mapping source id → source class.

Section A.3 of plugins/sources/CLAUDE.md.
Populated via inline imports in __init__.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crawler.plugins.sources._base import BaseSource

SOURCE_REGISTRY: dict[str, type[BaseSource]] = {}  # type: ignore[type-arg]
