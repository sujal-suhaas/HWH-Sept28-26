"""Frozen shared contract.

This is the interface the agent, the API, and the frontend all agree on. Freeze
it before building on top of it: changing it later means changing three places.

Deliberate design points:

* ``AlertPayload`` validates at the trust boundary, so a bad alert is a 4xx with
  a useful message rather than a crash deeper in the stack.
* ``Proposal`` has no path to authoritative memory. A diagnosis or resolution is
  always a proposal until an operator outcome turns it into one.
* ``FeedbackRequest`` validates the fields each feedback type actually requires,
  so a rejected diagnosis cannot arrive without the reason and a confirmed
  resolution cannot arrive without a validated fix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _now() -> datetime:
    return datetime.now(UTC)


class IncidentState(StrEnum):
    OPEN = "OPEN"
    DIAGNOSING = "DIAGNOSING"
    WAITING_FOR_OPERATOR = "WAITING_FOR_OPERATOR"
    RESOLVING = "RESOLVING"
    RESOLVED = "RESOLVED"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"


class Severity(StrEnum):
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"
    P4 = "p4"


class IncidentType(StrEnum):
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    SATURATION = "saturation"
    AVAILABILITY = "availability"
    DATA_CORRUPTION = "data_corruption"


class FeedbackType(StrEnum):
    DIAGNOSIS_CONFIRMED = "DIAGNOSIS_CONFIRMED"
    DIAGNOSIS_REJECTED = "DIAGNOSIS_REJECTED"
    OPERATOR_CORRECTION = "OPERATOR_CORRECTION"
    RESOLUTION_CONFIRMED = "RESOLUTION_CONFIRMED"
    RESOLUTION_FAILED = "RESOLUTION_FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class MemoryModeName(StrEnum):
    ON = "on"
    OFF = "off"


class AlertPayload(BaseModel):
    """The normalized alert that opens an incident."""

    service: str = Field(min_length=1)
    severity: Severity
    incident_type: IncidentType
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=2000)
    source: str = "pagerduty-sim"
    environment: str = "prod"
    fired_at: datetime = Field(default_factory=_now)
    error_samples: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("service", "source", "environment", "title", "summary")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class OpenIncidentRequest(BaseModel):
    """Body of ``POST /alerts``.

    ``memory_mode`` is a per-request override of the deployment default. The
    memory ON/OFF comparison requires the *same* alert run both ways, which is
    not possible if switching modes means restarting the service.
    """

    alert: AlertPayload
    memory_mode: MemoryModeName | None = None


class ChatRequest(BaseModel):
    """Body of ``POST /chat/{incident_id}``."""

    message: str = Field(min_length=1, max_length=2000)
    operator: str = Field(default="operator", min_length=1, max_length=120)

    @field_validator("message", "operator")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class Proposal(BaseModel):
    """A model proposal. Never authoritative memory."""

    kind: Literal["diagnosis", "resolution"]
    status: Literal["proposed", "pending_confirmation"]
    content: str
    evidence_summary: str
    cited_memory_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] | None = None
    runbook_id: str | None = None
    proposed_by: str = "agent"
    proposed_at: datetime = Field(default_factory=_now)


class TimelineEntry(BaseModel):
    at: datetime = Field(default_factory=_now)
    actor: Literal["pager", "agent", "operator", "system"]
    event: str
    detail: str


class FeedbackRequest(BaseModel):
    """Explicit operator outcome.

    Each feedback type has its own required fields; see the validator below.
    """

    feedback_type: FeedbackType
    operator: str = Field(min_length=1)
    note: str | None = None

    # DIAGNOSIS_CONFIRMED / OPERATOR_CORRECTION
    root_cause_id: str | None = None
    corrected_root_cause: str | None = None

    # RESOLUTION_CONFIRMED
    validated_fix: str | None = None
    validated_runbook_id: str | None = None

    @field_validator("operator")
    @classmethod
    def _operator_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("operator must not be blank")
        return stripped

    @model_validator(mode="after")
    def _require_fields_per_type(self) -> FeedbackRequest:
        if self.feedback_type in {
            FeedbackType.DIAGNOSIS_CONFIRMED,
            FeedbackType.OPERATOR_CORRECTION,
        } and not (self.root_cause_id or self.corrected_root_cause):
            raise ValueError(
                f"{self.feedback_type.value} requires root_cause_id or corrected_root_cause"
            )

        if self.feedback_type is FeedbackType.OPERATOR_CORRECTION and not self.corrected_root_cause:
            raise ValueError("OPERATOR_CORRECTION requires corrected_root_cause")

        if self.feedback_type is FeedbackType.RESOLUTION_CONFIRMED and not self.validated_fix:
            raise ValueError("RESOLUTION_CONFIRMED requires validated_fix")

        return self

    def is_authoritative_resolution(self) -> bool:
        """Only an explicit confirmation may create RESOLUTION memory."""
        return self.feedback_type is FeedbackType.RESOLUTION_CONFIRMED

    def is_authoritative_diagnosis(self) -> bool:
        return self.feedback_type in {
            FeedbackType.DIAGNOSIS_CONFIRMED,
            FeedbackType.DIAGNOSIS_REJECTED,
            FeedbackType.OPERATOR_CORRECTION,
        }


class IncidentResponse(BaseModel):
    """The full incident, as the UI consumes it."""

    incident_id: str
    state: IncidentState
    alert: AlertPayload
    timeline: list[TimelineEntry] = Field(default_factory=list)

    proposed_diagnosis: Proposal | None = None
    proposed_resolution: Proposal | None = None

    operator_outcome: FeedbackType | None = None
    operator: str | None = None
    root_cause_id: str | None = None
    validated_runbook_id: str | None = None

    memory_mode: MemoryModeName = MemoryModeName.ON
    model_used: str | None = None
    agent_status: str | None = None

    memory_trace_ids: list[str] = Field(default_factory=list)
    tool_trace_ids: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class IncidentListResponse(BaseModel):
    count: int
    incidents: list[IncidentResponse]


class ApiError(BaseModel):
    detail: str
    context: dict[str, Any] | None = None
