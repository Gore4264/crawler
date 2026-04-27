"""DecideStage — synthetic E1 decision stage.

Section B.4 of processing/CLAUDE.md.
Converts all surviving NormalizedMention objects into Signal objects
with synthetic relevance=1.0. Real LLM classification is E2b.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from crawler.core.models import NormalizedMention, PipelineTraceEntry, Signal

if TYPE_CHECKING:
    from crawler.processing.context import PipelineContext


class DecideStage:
    """
    Synthetic Decide stage for E1.

    For every surviving mention: creates a Signal with relevance_score=1.0,
    intent="other", is_spam=False, appends it to ctx.pending_signals.
    Returns empty list[] — all mentions are consumed here.

    Pipeline.run() collects signals from ctx.pending_signals after all stages
    complete.
    """

    name: str = "decide"

    async def process(
        self,
        mentions: list[NormalizedMention],
        ctx: PipelineContext,
    ) -> list[NormalizedMention]:
        now_utc = datetime.datetime.now(datetime.UTC)
        query_name = ctx.project.queries[0].name if ctx.project.queries else "default"

        for mention in mentions:
            # pipeline_trace must have min_length=1 per Signal model validator.
            # At this point ctx.trace contains entries from all prior stages.
            # If trace is empty (e.g. standalone test without preceding stages),
            # add a synthetic placeholder to satisfy the constraint.
            trace = list(ctx.trace)
            if not trace:
                trace = [
                    PipelineTraceEntry(
                        stage_name="decide",
                        started_at=now_utc,
                        duration_ms=0,
                        items_in=len(mentions),
                        items_out=len(mentions),
                    )
                ]

            signal = Signal(
                id=uuid4(),
                mention_id=mention.id,
                project_id=ctx.project.id,
                matched_query=query_name,
                relevance_score=1.0,
                is_spam=False,
                intent="other",
                sentiment="neutral",
                entities=[],
                topics=[],
                pipeline_trace=trace,
                cost_usd=Decimal("0"),
                created_at=now_utc,
            )
            ctx.pending_signals.append(signal)

        return []  # all mentions consumed; signals are in ctx.pending_signals
