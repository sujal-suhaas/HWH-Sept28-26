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
        """The event type, from metadata or the scoping tag.

        Hindsight's own derived observations carry no metadata, but they do keep
        the tags of the memory they came from, so the tag is a reliable fallback.
        """
        if self.metadata.get("event_type"):
            return self.metadata["event_type"]
        for tag in self.tags:
            if tag.startswith("event_type:"):
                return tag.split(":", 1)[1]
        return None

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
    #: Static mode label for the store: ``on`` or ``off``. Per-call failures are
    #: reported as ``degraded`` in the trace, not here.
    mode: str

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
        #: Override the relevance threshold. Pass 0.0 when the tag scope is exact
        #: and therefore is itself the relevance signal.
        min_score: float | None = None,
    ) -> RecallOutcome: ...

    def health(self) -> MemoryTrace: ...

    def close(self) -> None: ...
