"""In-memory :class:`MemoryStore` for tests and offline development.

Deliberately simple: tag filtering is exact and relevance is token overlap, so
tests can assert behaviour without a network or an LLM.
"""

from __future__ import annotations

import re
import time

from src.memory.interface import RecallHit, RecallOutcome
from src.memory.schema import MemoryEvent
from src.memory.trace import (
    ErrorCode,
    MemoryMode,
    MemoryOperation,
    MemoryTrace,
    utcnow,
)

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class InMemoryMemoryStore:
    """A fake that stores events in a list and scores recall by token overlap."""

    def __init__(
        self,
        bank_id: str = "dejaops-test",
        *,
        min_score: float = 0.2,
        fail_recall: bool = False,
        fail_retain: bool = False,
    ) -> None:
        self.bank_id = bank_id
        self.mode = MemoryMode.ON
        self.min_score = min_score
        self.fail_recall = fail_recall
        self.fail_retain = fail_retain
        self.events: list[MemoryEvent] = []
        self.retain_calls = 0
        self.recall_calls = 0
        #: Keyword arguments of every recall, so tests can assert on scoping.
        self.recall_kwargs: list[dict[str, object]] = []

    # --- helpers -----------------------------------------------------------
    def _latency(self, started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    # --- MemoryStore -------------------------------------------------------
    def create_bank_if_needed(self) -> MemoryTrace:
        started = utcnow()
        return MemoryTrace.build(
            operation=MemoryOperation.CREATE_BANK,
            mode=MemoryMode.ON,
            started_at=started,
            success=True,
            bank_id=self.bank_id,
        )

    def retain(self, event: MemoryEvent) -> MemoryTrace:
        started = utcnow()
        self.retain_calls += 1
        if self.fail_retain:
            return MemoryTrace.build(
                operation=MemoryOperation.RETAIN,
                mode=MemoryMode.DEGRADED,
                started_at=started,
                success=False,
                bank_id=self.bank_id,
                degraded=True,
                error_code=ErrorCode.UNAVAILABLE,
                error_message="fake store configured to fail retain",
            )
        self.events.append(event)
        return MemoryTrace.build(
            operation=MemoryOperation.RETAIN,
            mode=MemoryMode.ON,
            started_at=started,
            success=True,
            bank_id=self.bank_id,
            hit_count=1,
        )

    def recall(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        tags_match: str = "all",
        limit: int = 5,
        min_score: float | None = None,
    ) -> RecallOutcome:
        started = utcnow()
        self.recall_calls += 1
        threshold = self.min_score if min_score is None else min_score
        self.recall_kwargs.append(
            {
                "query": query,
                "tags": list(tags or []),
                "tags_match": tags_match,
                "limit": limit,
                "min_score": min_score,
                "threshold": threshold,
            }
        )
        if self.fail_recall:
            return RecallOutcome(
                hits=[],
                trace=MemoryTrace.build(
                    operation=MemoryOperation.RECALL,
                    mode=MemoryMode.DEGRADED,
                    started_at=started,
                    success=False,
                    bank_id=self.bank_id,
                    degraded=True,
                    error_code=ErrorCode.UNAVAILABLE,
                    error_message="fake store configured to fail recall",
                    query=query,
                    tags=tags or [],
                    no_match=True,
                ),
            )

        wanted = set(tags or [])
        query_tokens = _tokens(query)
        scored: list[tuple[float, MemoryEvent]] = []
        for event in self.events:
            event_tags = set(event.tags())
            if wanted:
                # "all" (the default) requires every supplied tag to be present.
                # "exact" is treated as a stricter "all" here; the fake does not
                # model tag-set equality.
                if tags_match in {"all", "exact", "all_strict"}:
                    if not wanted.issubset(event_tags):
                        continue
                elif not (wanted & event_tags):
                    continue
            overlap = query_tokens & _tokens(event.content)
            score = len(overlap) / len(query_tokens) if query_tokens else 0.0
            if score >= threshold:
                scored.append((score, event))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        hits = [
            RecallHit(
                memory_id=f"{event.incident_id}:{event.event_type.value}",
                text=event.content,
                fact_type="world",
                score=round(score, 4),
                tags=event.tags(),
                metadata=event.metadata(),
                document_id=event.document_id_or_default(),
                context=event.context,
                occurred_start=event.timestamp.isoformat(),
            )
            for score, event in scored[:limit]
        ]
        return RecallOutcome(
            hits=hits,
            trace=MemoryTrace.build(
                operation=MemoryOperation.RECALL,
                mode=MemoryMode.ON,
                started_at=started,
                success=True,
                bank_id=self.bank_id,
                hit_count=len(hits),
                query=query,
                tags=tags or [],
                min_score=threshold,
                no_match=not hits,
            ),
        )

    def health(self) -> MemoryTrace:
        started = utcnow()
        return MemoryTrace.build(
            operation=MemoryOperation.HEALTH,
            mode=MemoryMode.ON,
            started_at=started,
            success=True,
            bank_id=self.bank_id,
        )

    def close(self) -> None:
        return None
