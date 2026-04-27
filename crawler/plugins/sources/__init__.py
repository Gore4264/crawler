"""plugins/sources — inline-import registration of source plugins.

Section A.2 of plugins/sources/CLAUDE.md.
Importing this module populates SOURCE_REGISTRY with all available sources.
"""
from crawler.plugins.sources._base import BaseSource, BaseStreamingSource
from crawler.plugins.sources._registry import SOURCE_REGISTRY
from crawler.plugins.sources.reddit import RedditSource

# Register all sources
SOURCE_REGISTRY["reddit"] = RedditSource

__all__ = [
    "BaseSource",
    "BaseStreamingSource",
    "SOURCE_REGISTRY",
    "RedditSource",
]
