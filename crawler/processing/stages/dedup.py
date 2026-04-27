"""DedupStage — SHA-256 deduplication.

Section B.2 of processing/CLAUDE.md.
ADR-0004: content_hash = sha256(normalized_text), source not included.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from crawler.core.models import NormalizedMention

if TYPE_CHECKING:
    from crawler.processing.context import PipelineContext


class DedupStage:
    """
    SHA-256 deduplication stage.

    Algorithm:
    1. In-batch dedup: first-wins for duplicate content_hash within the batch.
    2. Query repository for hashes already present in the DB.
    3. Return only mentions whose content_hash is not in the DB set.

    Does NOT write to DB — that's the responsibility of the CLI/dispatcher
    integration layer (E1 integration session).
    No MinHash — minhash_signature field left as None (Phase 1+).
    """

    name: str = "dedup"

    async def process(
        self,
        mentions: list[NormalizedMention],
        ctx: PipelineContext,
    ) -> list[NormalizedMention]:
        # Step 1: in-batch dedup — first-wins
        seen: set[str] = set()
        deduped_batch: list[NormalizedMention] = []
        for mention in mentions:
            if mention.content_hash not in seen:
                seen.add(mention.content_hash)
                deduped_batch.append(mention)

        if not deduped_batch:
            return []

        # Step 2: query DB (or FakeRepository) for already-existing hashes
        existing = await ctx.repository.existing_hashes(list(seen))

        # Step 3: filter out already-seen
        result = [m for m in deduped_batch if m.content_hash not in existing]

        # Populate ctx.surviving_mentions (used by api_core/scanning.py)
        ctx.surviving_mentions.extend(result)
        return result
