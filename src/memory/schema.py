"""Durable memory event schema.

Only backend-controlled lifecycle events may build a :class:`MemoryEvent`.
The model may *propose* a diagnosis or resolution, but proposals live in the
incident timeline and never reach the memory layer until an operator outcome
turns them into one of the event types below.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class EventType(StrEnum):
    """Qualifying durable memory categories."""

    INCIDENT_OPEN = "INCIDENT_OPEN"
    DIAGNOSIS = "DIAGNOSIS"
    RESOLUTION = "RESOLUTION"
    POSTMORTEM = "POSTMORTEM"
    RUNBOOK_ENTRY = "RUNBOOK_ENTRY"
    OPERATOR_CORRECTION = "OPERATOR_CORRECTION"


class Outcome(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryEvent(BaseModel):
    """A single durable operational fact to retain.

    ``tags`` scope recall (see :meth:`tags`); ``metadata`` travels with the
    recalled memory as context (see :meth:`metadata`). The two are deliberately
    different mechanisms and must not be conflated.
    """

    event_type: EventType
    incident_id: str
    content: str
    context: str | None = None

    service: str | None = None
    severity: str | None = None
    incident_type: str | None = None
    environment: str = "prod"

    source: str = "dejaops-backend"
    timestamp: datetime = Field(default_factory=_now)
    document_id: str | None = None
    runbook_id: str | None = None
    outcome: Outcome | None = None

    @field_validator("content", "incident_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    def tags(self) -> list[str]:
        """Recall-scoping tags. These are the only values used to filter recall."""
        tags = [f"environment:{self.environment}"]
        if self.service:
            tags.append(f"service:{self.service}")
        if self.severity:
            tags.append(f"severity:{self.severity.lower()}")
        if self.incident_type:
            tags.append(f"incident_type:{self.incident_type.lower()}")
        # Scoping tag so runbook-only recall is possible. AGENTS.md lists
        # event_type under metadata; it is duplicated here because metadata is
        # not a recall filter.
        tags.append(f"event_type:{self.event_type.value.lower()}")
        return tags

    def metadata(self) -> dict[str, str]:
        """Context carried with the memory. Never used for filtering."""
        meta = {
            "incident_id": self.incident_id,
            "event_type": self.event_type.value,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.runbook_id:
            meta["runbook_id"] = self.runbook_id
        if self.outcome:
            meta["outcome"] = self.outcome.value
        return meta

    def document_id_or_default(self) -> str:
        return self.document_id or f"{self.incident_id}:{self.event_type.value.lower()}"


def runbook_tags(service: str | None, incident_type: str | None) -> list[str]:
    """Scoping tags for a runbook lookup."""
    tags = ["event_type:runbook_entry"]
    if service:
        tags.append(f"service:{service}")
    if incident_type:
        tags.append(f"incident_type:{incident_type.lower()}")
    return tags
