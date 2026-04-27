"""PipelineContext — runtime context for one pipeline run.

Section A.2 of processing/CLAUDE.md.

Lives in processing/, not core/, because it contains DI handles on IRepository
(and future IEmbedder, IClassifier). Moving to core/ would violate the rule
that core/ has no internal dependencies.
"""
from __future__ import annotations

import dataclasses
from uuid import UUID

from crawler.core.contracts import IRepository
from crawler.core.models import NormalizedMention, PipelineTraceEntry, Project, Signal


@dataclasses.dataclass
class PipelineContext:
    """
    Runtime context for one pipeline run. Passed to all stages.

    Stages can:
      - read: project, repository, scan_id
      - append: trace entries (via add_trace), pending_signals
      - populate: all_normalized (NormalizeStage), surviving_mentions (DedupStage)
    """

    project: Project
    scan_id: UUID
    repository: IRepository
    trace: list[PipelineTraceEntry] = dataclasses.field(default_factory=list)
    pending_signals: list[Signal] = dataclasses.field(default_factory=list)
    # NEW: populated by NormalizeStage — all normalized mentions before dedup
    all_normalized: list[NormalizedMention] = dataclasses.field(default_factory=list)
    # NEW: populated by DedupStage — mentions that survived dedup (new to DB)
    surviving_mentions: list[NormalizedMention] = dataclasses.field(default_factory=list)

    def add_trace(
        self,
        stage_name: str,
        in_count: int,
        out_count: int,
        duration_ms: float,
        cost_usd: float | None = None,
    ) -> None:
        """Create a PipelineTraceEntry and append to trace list."""
        import datetime
        from decimal import Decimal

        entry = PipelineTraceEntry(
            stage_name=stage_name,
            started_at=datetime.datetime.now(datetime.UTC),
            duration_ms=int(duration_ms),
            items_in=in_count,
            items_out=out_count,
            cost_usd=Decimal(str(cost_usd)) if cost_usd is not None else Decimal("0"),
        )
        self.trace.append(entry)
