"""Memory-OFF store: performs no Hindsight calls whatsoever.

Used for the memory ON/OFF comparison so the only variable is memory
availability. Every call returns a trace labelled ``mode=off`` and an explicit
"memory is off" error code - it never pretends a memory was found.
"""

from __future__ import annotations

from src.memory.interface import RecallOutcome
from src.memory.schema import MemoryEvent
from src.memory.trace import ErrorCode, MemoryMode, MemoryOperation, MemoryTrace, utcnow


class DisabledMemoryStore:
    """A :class:`~src.memory.interface.MemoryStore` that does nothing."""

    def __init__(self, bank_id: str) -> None:
        self.bank_id = bank_id
        self.mode = MemoryMode.OFF

    def _trace(self, operation: MemoryOperation) -> MemoryTrace:
        return MemoryTrace.build(
            operation=operation,
            mode=MemoryMode.OFF,
            started_at=utcnow(),
            success=False,
            bank_id=self.bank_id,
            error_code=ErrorCode.MEMORY_OFF,
            error_message="memory_mode=off: Hindsight was not called",
            no_match=operation is MemoryOperation.RECALL,
        )

    def create_bank_if_needed(self) -> MemoryTrace:
        return self._trace(MemoryOperation.CREATE_BANK)

    def retain(self, event: MemoryEvent) -> MemoryTrace:
        return self._trace(MemoryOperation.RETAIN)

    def recall(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        tags_match: str = "all",
        limit: int = 5,
        min_score: float | None = None,
    ) -> RecallOutcome:
        trace = self._trace(MemoryOperation.RECALL)
        trace.query = query
        trace.tags = tags or []
        return RecallOutcome(hits=[], trace=trace)

    def health(self) -> MemoryTrace:
        return self._trace(MemoryOperation.HEALTH)

    def close(self) -> None:
        return None
