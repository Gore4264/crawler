"""Unit tests for RedditSource — uses unittest.mock to avoid real API calls."""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from crawler.core.contracts import SourceCapabilities
from crawler.core.models import RawMention
from crawler.plugins.sources import SOURCE_REGISTRY, RedditSource
from crawler.plugins.sources.reddit import RedditConfig

UTC = datetime.UTC


def make_config() -> RedditConfig:
    return RedditConfig(
        client_id="fake_client_id",
        client_secret=SecretStr("fake_secret"),
        user_agent="test/0.1",
        subreddits=["ClaudeAI"],
    )


def make_mock_submission(
    fullname: str = "t3_abc123",
    title: str = "Test post title",
    selftext: str = "",
    selftext_html: str | None = None,
    permalink: str = "/r/ClaudeAI/comments/abc123/test_post_title/",
    score: int = 42,
    num_comments: int = 5,
    upvote_ratio: float = 0.95,
    created_utc: float | None = None,
    author_name: str = "test_user",
    subreddit_name: str = "ClaudeAI",
    is_self: bool = True,
    over_18: bool = False,
    link_flair_text: str | None = None,
) -> MagicMock:
    """Create a mock PRAW Submission object."""
    sub = MagicMock()
    sub.fullname = fullname
    sub.id = fullname.replace("t3_", "")
    sub.title = title
    sub.selftext = selftext
    sub.selftext_html = selftext_html
    sub.permalink = permalink
    sub.score = score
    sub.num_comments = num_comments
    sub.upvote_ratio = upvote_ratio
    sub.created_utc = created_utc or datetime.datetime.now(UTC).timestamp()
    sub.is_self = is_self
    sub.over_18 = over_18
    sub.link_flair_text = link_flair_text

    # Author mock
    author = MagicMock()
    author.__str__ = lambda self: author_name
    sub.author = author

    # Subreddit mock
    subreddit = MagicMock()
    subreddit.display_name = subreddit_name
    sub.subreddit = subreddit

    return sub


# ---------------------------------------------------------------------------
# Criterion 7: capabilities.supports_streaming is False
# ---------------------------------------------------------------------------


def test_reddit_source_supports_streaming_is_false():
    """Criterion 7: RedditSource.capabilities.supports_streaming is False."""
    assert RedditSource.capabilities.supports_streaming is False


def test_reddit_source_capabilities():
    """RedditSource has expected capabilities."""
    caps = RedditSource.capabilities
    assert isinstance(caps, SourceCapabilities)
    assert caps.supports_keywords is True
    assert caps.cost_model == "free"
    assert caps.supports_streaming is False


# ---------------------------------------------------------------------------
# Criterion 5: SOURCE_REGISTRY
# ---------------------------------------------------------------------------


def test_source_registry_contains_reddit():
    """Criterion 5: SOURCE_REGISTRY['reddit'] is RedditSource."""
    assert "reddit" in SOURCE_REGISTRY
    assert SOURCE_REGISTRY["reddit"] is RedditSource


# ---------------------------------------------------------------------------
# Initialization with mocked PRAW
# ---------------------------------------------------------------------------


def test_reddit_source_init():
    """RedditSource can be instantiated (PRAW constructor mocked)."""
    config = make_config()
    with patch("praw.Reddit") as mock_reddit_cls:
        source = RedditSource(config)
        mock_reddit_cls.assert_called_once_with(
            client_id="fake_client_id",
            client_secret="fake_secret",
            user_agent="test/0.1",
        )
        assert source.id == "reddit"


# ---------------------------------------------------------------------------
# Mapping: Submission → RawMention
# ---------------------------------------------------------------------------


def test_map_submission_selftext():
    """Submission with selftext → text is selftext."""
    config = make_config()
    with patch("praw.Reddit"):
        source = RedditSource(config)

    sub = make_mock_submission(
        fullname="t3_xyz",
        selftext="This is the post body content about AI",
        title="Post title",
    )
    mention = source._map_submission(sub)

    assert isinstance(mention, RawMention)
    assert mention.text == "This is the post body content about AI"
    assert mention.source_id == "reddit"
    assert mention.external_id == "t3_xyz"


def test_map_submission_link_post_uses_title():
    """Link post (empty selftext) → text is title."""
    config = make_config()
    with patch("praw.Reddit"):
        source = RedditSource(config)

    sub = make_mock_submission(
        fullname="t3_link",
        selftext="",  # empty for link posts
        title="Interesting article about Claude",
    )
    mention = source._map_submission(sub)
    assert mention.text == "Interesting article about Claude"


def test_map_submission_url():
    """URL is constructed as https://www.reddit.com + permalink."""
    config = make_config()
    with patch("praw.Reddit"):
        source = RedditSource(config)

    sub = make_mock_submission(
        permalink="/r/ClaudeAI/comments/abc123/test/"
    )
    mention = source._map_submission(sub)
    assert "www.reddit.com" in str(mention.url)
    assert "/r/ClaudeAI/comments/abc123/test/" in str(mention.url)


def test_map_submission_published_at_is_utc():
    """published_at is a UTC-aware datetime."""
    config = make_config()
    with patch("praw.Reddit"):
        source = RedditSource(config)

    ts = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC).timestamp()
    sub = make_mock_submission(created_utc=ts)
    mention = source._map_submission(sub)

    assert mention.published_at.tzinfo is not None
    assert mention.published_at.year == 2024


def test_map_submission_engagement():
    """Engagement dict has score, num_comments, upvote_ratio as int."""
    config = make_config()
    with patch("praw.Reddit"):
        source = RedditSource(config)

    sub = make_mock_submission(score=100, num_comments=25, upvote_ratio=0.9)
    mention = source._map_submission(sub)

    assert mention.engagement["score"] == 100
    assert mention.engagement["num_comments"] == 25
    assert mention.engagement["upvote_ratio"] == 90  # 0.9 * 100 as int


def test_map_submission_author_none():
    """Submission with no author (deleted) → author is None."""
    config = make_config()
    with patch("praw.Reddit"):
        source = RedditSource(config)

    sub = make_mock_submission()
    sub.author = None
    mention = source._map_submission(sub)

    assert mention.author is None
    assert mention.author_id is None


def test_map_submission_raw_data():
    """Raw field contains subreddit, is_self, over_18, flair."""
    config = make_config()
    with patch("praw.Reddit"):
        source = RedditSource(config)

    sub = make_mock_submission(
        subreddit_name="ClaudeAI",
        is_self=True,
        over_18=False,
        link_flair_text="Discussion",
    )
    mention = source._map_submission(sub)

    assert mention.raw["subreddit"] == "ClaudeAI"
    assert mention.raw["is_self"] is True
    assert mention.raw["over_18"] is False
    assert mention.raw["flair"] == "Discussion"


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


def test_estimate_cost_free():
    """estimate_cost returns CostEstimate with 0 USD."""
    config = make_config()
    with patch("praw.Reddit"):
        source = RedditSource(config)

    from crawler.core.models import SourceQuery

    q = SourceQuery(keywords=["anthropic"], limit=50)
    estimate = source.estimate_cost(q)

    assert estimate.expected_cost_usd == Decimal("0")
    assert estimate.confidence == "exact"
    assert estimate.expected_results == 50


# ---------------------------------------------------------------------------
# search() — mock PRAW call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_yields_raw_mentions():
    """search() yields one RawMention per submission returned by PRAW."""
    config = make_config()

    mock_submissions = [
        make_mock_submission(fullname=f"t3_{i:03d}", selftext=f"Post content {i} about AI")
        for i in range(3)
    ]

    with patch("praw.Reddit") as mock_reddit_cls:
        mock_reddit_instance = mock_reddit_cls.return_value
        mock_subreddit = MagicMock()
        mock_reddit_instance.subreddit.return_value = mock_subreddit
        mock_subreddit.search.return_value = mock_submissions

        source = RedditSource(config)
        from crawler.core.models import SourceQuery

        q = SourceQuery(keywords=["claude"], limit=3)
        results = []
        async for mention in source.search(q):
            results.append(mention)

    assert len(results) == 3
    for i, mention in enumerate(results):
        assert isinstance(mention, RawMention)
        assert mention.source_id == "reddit"
        assert f"t3_{i:03d}" == mention.external_id


@pytest.mark.asyncio
async def test_search_passes_since_cursor():
    """search() passes since_cursor as 'after' param to PRAW."""
    config = make_config()
    mock_submissions = [make_mock_submission()]

    captured_params = {}

    def mock_search(keyword, sort, limit, params):
        captured_params.update(params)
        return mock_submissions

    with patch("praw.Reddit") as mock_reddit_cls:
        mock_reddit_instance = mock_reddit_cls.return_value
        mock_subreddit = MagicMock()
        mock_reddit_instance.subreddit.return_value = mock_subreddit
        mock_subreddit.search.side_effect = mock_search

        source = RedditSource(config)
        from crawler.core.models import SourceQuery

        q = SourceQuery(keywords=["claude"], since_cursor="t3_lastpost", limit=10)
        async for _ in source.search(q):
            pass

    assert captured_params.get("after") == "t3_lastpost"


@pytest.mark.asyncio
async def test_search_no_cursor():
    """search() with no since_cursor passes empty params dict to PRAW."""
    config = make_config()
    mock_submissions = [make_mock_submission()]

    captured_params = {}

    def mock_search(keyword, sort, limit, params):
        captured_params.update(params)
        return mock_submissions

    with patch("praw.Reddit") as mock_reddit_cls:
        mock_reddit_instance = mock_reddit_cls.return_value
        mock_subreddit = MagicMock()
        mock_reddit_instance.subreddit.return_value = mock_subreddit
        mock_subreddit.search.side_effect = mock_search

        source = RedditSource(config)
        from crawler.core.models import SourceQuery

        q = SourceQuery(keywords=["claude"])
        async for _ in source.search(q):
            pass

    assert "after" not in captured_params
