"""Scanning service — orchestrates source + pipeline + persistence.

run_scan() is the main entry-point called by `crawler scan` command.
All Reddit source construction happens here via _get_reddit_source().

E1 scope: only Reddit source supported. Pipeline stages are hardcoded.
"""
from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from crawler.api_core.exceptions import ProjectNotFoundError, RedditCredentialsMissingError
from crawler.core.contracts import IRepository
from crawler.core.models import SourceQuery


@dataclasses.dataclass
class ScanResult:
    """Statistics from one scan run (per source/query pair)."""

    project_id: str
    source_id: str
    query_name: str
    mentions_fetched: int
    mentions_inserted: int
    duplicates: int
    signals_created: int
    cost_usd: Decimal
    duration_seconds: float
    status: str  # ScanStatus literal: 'ok' | 'partial' | 'failed'


async def run_scan(
    repo: IRepository,
    project_id: str,
    *,
    limit: int = 100,
    progress_callback: Callable[[str], None] | None = None,
    source_factory: Callable | None = None,
) -> list[ScanResult]:
    """
    Run a scan for a project against all its queries × sources.

    For each TopicQuery in project.queries:
      1. Validate Reddit credentials from env.
      2. Initialise RedditSource.
      3. Fetch raw mentions via source.search(SourceQuery).
      4. Run through Pipeline (NormalizeStage → DedupStage → KeywordFilterStage → DecideStage).
      5. Persist all_normalized via repo.bulk_upsert_mentions_with_dedup.
      6. Persist signals via repo.insert_signals.
      7. Record scan log entry.
      8. Record usage entry.

    Returns: list[ScanResult] — one per (query, source) pair.
    """
    project = await repo.get_project(project_id)
    if project is None:
        raise ProjectNotFoundError(project_id)

    results: list[ScanResult] = []

    for topic_query in project.queries:
        t_start = time.perf_counter()
        scan_id = uuid4()
        started_at = datetime.now(UTC)
        status = "ok"
        signals_created = 0
        mentions_fetched = 0
        inserted = 0
        skipped = 0

        try:
            if source_factory is not None:
                source = source_factory(project)
            else:
                source = _get_reddit_source()

            if progress_callback:
                progress_callback(f"Fetching from reddit (query={topic_query.name})...")

            source_query = SourceQuery(
                keywords=topic_query.keywords,
                excluded_keywords=topic_query.excluded_keywords,
                limit=limit,
                mode="search",
            )

            raw_mentions = [m async for m in source.search(source_query)]
            mentions_fetched = len(raw_mentions)

            if progress_callback:
                progress_callback("Running pipeline...")

            pipeline = _build_pipeline(repo)
            signals = await pipeline.run(raw_mentions, project, scan_id)
            ctx = pipeline.last_ctx

            if ctx is not None:
                all_normalized = ctx.all_normalized
                inserted, skipped = await repo.bulk_upsert_mentions_with_dedup(
                    all_normalized
                )
            else:
                inserted = 0
                skipped = 0

            signals_created = await repo.insert_signals(signals)

        except Exception:
            status = "failed"
            raise

        finally:
            finished_at = datetime.now(UTC)
            duration = time.perf_counter() - t_start
            cost_usd = Decimal("0")

            await repo.record_scan(
                scan_id=scan_id,
                project_id=project_id,
                source_id="reddit",
                query_name=topic_query.name,
                started_at=started_at,
                finished_at=finished_at,
                count=mentions_fetched,
                cost_usd=cost_usd,
                status=status,  # type: ignore[arg-type]
            )
            await repo.append_usage(
                project_id=project_id,
                source_id="reddit",
                cost_usd=cost_usd,
                occurred_at=started_at,
                kind="source",
            )

            results.append(
                ScanResult(
                    project_id=project_id,
                    source_id="reddit",
                    query_name=topic_query.name,
                    mentions_fetched=mentions_fetched,
                    mentions_inserted=inserted,
                    duplicates=skipped,
                    signals_created=signals_created,
                    cost_usd=cost_usd,
                    duration_seconds=duration,
                    status=status,
                )
            )

    return results


def _get_reddit_source():
    """
    Read Reddit credentials from env, validate, and return a RedditSource.
    Raises RedditCredentialsMissingError on missing vars.
    """
    from crawler.plugins.sources.reddit import RedditConfig, RedditSource

    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        if not os.getenv(var):
            raise RedditCredentialsMissingError(var)

    from pydantic import SecretStr

    config = RedditConfig(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=SecretStr(os.environ["REDDIT_CLIENT_SECRET"]),
        user_agent=os.environ["REDDIT_USER_AGENT"],
        default_limit=100,
    )
    return RedditSource(config=config)


def _build_pipeline(repo: IRepository):
    """
    Build the E1 pipeline with hardcoded stages.
    project.pipeline field is stored in JSONB but ignored here (Phase 0).
    """
    from crawler.processing.pipeline import Pipeline
    from crawler.processing.stages.decide import DecideStage
    from crawler.processing.stages.dedup import DedupStage
    from crawler.processing.stages.keyword_filter import KeywordFilterStage
    from crawler.processing.stages.normalize import NormalizeStage

    return Pipeline(
        stages=[
            NormalizeStage(),
            DedupStage(),
            KeywordFilterStage(),
            DecideStage(),
        ],
        repository=repo,
    )
