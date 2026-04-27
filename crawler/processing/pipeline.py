"""Pipeline — chain of IStage instances.

Section A.1 of processing/CLAUDE.md.
"""
from __future__ import annotations

import datetime
import time
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from crawler.core.contracts import IRepository, IStage
from crawler.core.models import NormalizedMention, PipelineTraceEntry, Project, RawMention, Signal

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class Pipeline:
    """
    Chain of IStage instances. Accepts a batch of RawMention or NormalizedMention
    objects, runs them through all stages, and returns list[Signal].

    The first stage (NormalizeStage) handles the RawMention → NormalizedMention
    conversion. Subsequent stages work on NormalizedMention.

    Pipeline(stages, repository).run(mentions, project) → list[Signal]
    """

    def __init__(
        self,
        stages: list[IStage],
        repository: IRepository,
    ) -> None:
        self._stages = stages
        self._repository = repository

    async def run(
        self,
        mentions: list[RawMention],
        project: Project,
        scan_id: UUID | None = None,
    ) -> list[Signal]:
        """
        Run a batch of raw mentions through the full pipeline.

        The first stage (NormalizeStage) accepts list[RawMention] via duck-typing —
        it handles the type conversion internally. All subsequent stages receive
        list[NormalizedMention].

        Returns list[Signal] collected from ctx.pending_signals (populated by
        DecideStage).
        """
        from crawler.processing.context import PipelineContext

        sid = scan_id or uuid4()
        ctx = PipelineContext(
            project=project,
            scan_id=sid,
            repository=self._repository,
        )

        # Pipeline processes NormalizedMention after NormalizeStage.
        # We start with RawMention and use duck-typing: NormalizeStage accepts
        # the raw list and converts internally.
        current: list[NormalizedMention] = mentions  # type: ignore[assignment]

        for stage in self._stages:
            items_in = len(current)
            started_at = datetime.datetime.now(datetime.UTC)
            t0 = time.perf_counter()

            current = await stage.process(current, ctx)  # type: ignore[arg-type]

            duration_ms = int((time.perf_counter() - t0) * 1000)

            entry = PipelineTraceEntry(
                stage_name=stage.name,
                started_at=started_at,
                duration_ms=duration_ms,
                items_in=items_in,
                items_out=len(current),
                cost_usd=Decimal("0"),  # E1 stages are free; LLM stages will update
            )
            ctx.trace.append(entry)

            logger.info(
                "pipeline_stage_complete",
                stage=stage.name,
                items_in=items_in,
                items_out=len(current),
                duration_ms=duration_ms,
                scan_id=str(sid),
                project_id=project.id,
            )

            if not current:
                logger.info(
                    "pipeline_early_exit",
                    stage=stage.name,
                    reason="all_mentions_filtered",
                    scan_id=str(sid),
                )
                break

        return ctx.pending_signals
