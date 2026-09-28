"""The memory interface the rest of the application depends on.

Nothing outside :mod:`src.memory.hindsight_client` may import the Hindsight
SDK. Everything else talks to :class:`MemoryStore`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from src.memory.schema import MemoryEvent
from src.memory.trace import MemoryTrace


class MemoryStoreError(RuntimeError):
    """Raised only for programming errors (bad arguments), never for outages.

    Provider failures are reported through :class:`MemoryTrace` so the caller
    can degrade honestly instead of crashing.
    """


class RecallHit(BaseModel):
    """One normalized recalled memory."""

    memory_id: str
    text: str
    fact_type: str | None = None
    score: float | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    document_id: str | None = None
    context: str | None = None
    occurred_start: str | None = None

    @property
    def incident_id(self) -> str | None:
        return self.metadata.get("incident_id")

    @property
    def event_type(self) -> str | None:
        return self.metadata.get("event_type")

    @property
    def runbook_id(self) -> str | None:
        return self.metadata.get("runbook_id")


class RecallOutcome(BaseModel):
    """Result of a recall attempt, including its trace."""

    hits: list[RecallHit] = Field(default_factory=list)
    trace: MemoryTrace

    @property
    def no_match(self) -> bool:
        return not self.hits


@runtime_checkable
class MemoryStore(Protocol):
    """Storage-agnostic memory operations."""

    bank_id: str

    def create_bank_if_needed(self) -> MemoryTrace: ...

    def retain(self, event: MemoryEvent) -> MemoryTrace: ...

    def recall(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        # Our tags express a *scope*, so every supplied tag must match.
        tags_match: str = "all",
        limit: int = 5,
    ) -> RecallOutcome: ...

    def health(self) -> MemoryTrace: ...

    def close(self) -> None: ...
