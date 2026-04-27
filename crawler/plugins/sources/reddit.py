"""RedditSource — PRAW-based Reddit data source plugin.

Section C of plugins/sources/CLAUDE.md.
"""
from __future__ import annotations

import asyncio
import datetime
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, Field, HttpUrl, SecretStr

from crawler.core.contracts import CostEstimate, SourceCapabilities
from crawler.core.models import RawMention, SourceQuery
from crawler.plugins.sources._base import BaseSource

# ---------------------------------------------------------------------------
# C.1. Configuration
# ---------------------------------------------------------------------------


class RedditConfig(BaseModel):
    """Configuration for the Reddit source. Loaded from YAML/env in E2c."""

    client_id: str
    client_secret: SecretStr
    user_agent: str = "crawler/0.1 by crawler_bot"
    subreddits: list[str] = Field(default_factory=lambda: ["ClaudeAI"])
    default_sort: str = "new"  # new | hot | top | relevance
    default_limit: int = 100


# ---------------------------------------------------------------------------
# C.2–C.7. RedditSource
# ---------------------------------------------------------------------------


class RedditSource(BaseSource[RedditConfig]):
    """
    Reddit source using PRAW (synchronous) + asyncio.to_thread() for async wrapping.

    Decision F.1: praw (sync) + asyncio.to_thread() chosen over asyncpraw
    to avoid asyncpraw API inconsistencies; to_thread is standard Python 3.9+.
    """

    id: ClassVar[str] = "reddit"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_keywords=True,
        supports_semantic=False,
        supports_geo=False,
        supports_language_filter=False,
        supports_search=True,
        supports_streaming=False,  # ADR-0002: Reddit is REST-pull
        supports_historical=True,
        cost_model="free",
        typical_latency_ms=2000,
    )

    def __init__(self, config: RedditConfig) -> None:
        super().__init__(config)
        import praw  # local import to avoid hard dep at module import time

        self._praw = praw.Reddit(
            client_id=config.client_id,
            client_secret=config.client_secret.get_secret_value(),
            user_agent=config.user_agent,
        )

    # C.3. search()
    async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]:  # type: ignore[override]
        """
        Search Reddit for mentions matching the query.

        Paginates through self._config.subreddits sequentially.
        since_cursor: "t3_xxx" fullname for Reddit pagination via `after` param.
        """
        keyword = " ".join(q.keywords) if q.keywords else ""
        limit = q.limit
        after = q.since_cursor  # None or "t3_xxx"

        for subreddit_name in self._config.subreddits:
            async with self._limiter:
                params: dict[str, str] = {}
                if after:
                    params["after"] = after

                # Capture locals to avoid late-binding in lambda
                _praw = self._praw
                _subreddit_name = subreddit_name
                _keyword = keyword
                _limit = limit
                _params = params

                submissions = await asyncio.to_thread(
                    lambda: list(
                        _praw.subreddit(_subreddit_name).search(
                            _keyword,
                            sort="new",
                            limit=_limit,
                            params=_params,
                        )
                    )
                )

            for submission in submissions:
                yield self._map_submission(submission)

    # C.4. Mapping
    def _map_submission(self, submission: object) -> RawMention:  # type: ignore[type-arg]
        """Map a PRAW Submission object to RawMention."""
        now_utc = datetime.datetime.now(datetime.UTC)

        # Access PRAW attributes dynamically (praw is untyped)
        sub = submission  # type: ignore[assignment]

        author_name: str | None = None
        if sub.author is not None:  # type: ignore[union-attr]
            try:
                author_name = str(sub.author)  # type: ignore[union-attr]
            except Exception:
                author_name = None

        text_raw: str = sub.selftext or sub.title  # type: ignore[union-attr]
        text_html_raw: str | None = sub.selftext_html or None  # type: ignore[union-attr]

        engagement: dict[str, int] = {
            "score": int(sub.score),  # type: ignore[union-attr]
            "num_comments": int(sub.num_comments),  # type: ignore[union-attr]
            "upvote_ratio": int(sub.upvote_ratio * 100),  # type: ignore[union-attr]
        }

        raw_data: dict = {  # type: ignore[type-arg]
            "id": sub.id,  # type: ignore[union-attr]
            "fullname": sub.fullname,  # type: ignore[union-attr]
            "subreddit": sub.subreddit.display_name,  # type: ignore[union-attr]
            "is_self": sub.is_self,  # type: ignore[union-attr]
            "over_18": sub.over_18,  # type: ignore[union-attr]
            "flair": sub.link_flair_text,  # type: ignore[union-attr]
        }

        published_at = datetime.datetime.fromtimestamp(
            sub.created_utc,  # type: ignore[union-attr]
            tz=datetime.UTC,
        )

        permalink: str = sub.permalink  # type: ignore[union-attr]
        url_str = f"https://www.reddit.com{permalink}"

        return RawMention(
            source_id=self.id,
            external_id=str(sub.fullname),  # type: ignore[union-attr]  # "t3_xxx"
            author=author_name,
            author_id=author_name,
            text=text_raw,
            text_html=text_html_raw,
            url=HttpUrl(url_str),
            lang_hint=None,
            engagement=engagement,
            raw=raw_data,
            published_at=published_at,
            discovered_at=now_utc,
            fetched_at=now_utc,
        )

    # C.6. health_check()
    async def health_check(self) -> bool:
        """
        Verify that Reddit OAuth credentials are valid by calling /api/v1/me.
        Returns True if authenticated, False otherwise.
        """
        try:
            _praw = self._praw
            me = await asyncio.to_thread(lambda: _praw.user.me())
            return me is not None
        except Exception:
            return False

    # C.7. estimate_cost()
    def estimate_cost(self, q: SourceQuery) -> CostEstimate:
        """Reddit OAuth API is free — cost is always 0."""
        return CostEstimate(
            expected_results=q.limit,
            expected_cost_usd=Decimal("0"),
            confidence="exact",
        )
