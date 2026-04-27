"""KeywordFilterStage — regex-based keyword filtering.

Section B.3 of processing/CLAUDE.md.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from crawler.core.models import NormalizedMention

if TYPE_CHECKING:
    from crawler.processing.context import PipelineContext


def _compile_keyword(kw: str) -> re.Pattern[str]:
    """
    Compile one keyword into a regex pattern.

    Strategy:
    - Multi-word phrases → verbatim substring (case-insensitive)
    - Long single words (> 3 chars) → word-boundary: r"\\bword\\b"
    - Short words (≤ 3 chars) → substring (no word boundary, avoids breaking
      abbreviations like "AI" in "AI-driven")

    All patterns use IGNORECASE (text_clean is already lowercase, but this is
    defensive for future changes).
    """
    escaped = re.escape(kw.lower().strip())
    words = kw.split()
    if len(words) > 1:
        # Multi-word: verbatim substring
        return re.compile(escaped, re.IGNORECASE)
    elif len(kw.strip()) > 3:
        # Single long word: word-boundary
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    else:
        # Short word (≤ 3 chars): substring
        return re.compile(escaped, re.IGNORECASE)


class KeywordFilterStage:
    """
    Regex-based keyword filter.

    Compiles patterns lazily at first process() call for the given project.
    Caches patterns by project.id.

    Empty keywords → no-op (pass everything through). Decision F.1:
    absence of constraint = no restriction.
    """

    name: str = "keyword_filter"

    def __init__(self) -> None:
        # Cache: project_id → list of (include_patterns, exclude_patterns) per query
        self._compiled: dict[str, list[tuple[list[re.Pattern[str]], list[re.Pattern[str]]]]] = {}

    def _get_patterns(
        self,
        project: object,
    ) -> list[tuple[list[re.Pattern[str]], list[re.Pattern[str]]]]:
        """Return (include, exclude) pattern lists for each TopicQuery in project."""
        from crawler.core.models import Project

        proj: Project = project  # type: ignore[assignment]
        pid = proj.id
        if pid not in self._compiled:
            compiled: list[tuple[list[re.Pattern[str]], list[re.Pattern[str]]]] = []
            for query in proj.queries:
                include_pats = [_compile_keyword(kw) for kw in query.keywords]
                exclude_pats = [_compile_keyword(kw) for kw in query.excluded_keywords]
                compiled.append((include_pats, exclude_pats))
            self._compiled[pid] = compiled
        return self._compiled[pid]

    async def process(
        self,
        mentions: list[NormalizedMention],
        ctx: PipelineContext,
    ) -> list[NormalizedMention]:
        """
        Filter mentions: keep those matching at least one TopicQuery's
        include keywords and not matching any of its excluded keywords.

        OR semantics across queries (a mention passes if it matches any query).
        """
        patterns = self._get_patterns(ctx.project)
        result: list[NormalizedMention] = []

        for mention in mentions:
            text = mention.text_clean
            passed = False
            for include_pats, exclude_pats in patterns:
                # Empty include → no-op (pass all)
                include_match = (
                    not include_pats or any(p.search(text) for p in include_pats)
                )
                exclude_match = any(p.search(text) for p in exclude_pats)
                if include_match and not exclude_match:
                    passed = True
                    break
            if passed:
                result.append(mention)

        return result
