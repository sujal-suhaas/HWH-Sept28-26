"""Trace records for agent runs.

Every model call and every tool call is recorded, including the failures and the
repairs. The UI shows these instead of hidden model reasoning: concise evidence,
tool results, memory ids, and outcome summaries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    TOOL_CALL_INVALID = "tool_call_invalid"
    MODEL_FAILED = "model_failed"


class ModelRole(StrEnum):
    PRIMARY = "primary"
    FALLBACK = "fallback"


class ToolCall(BaseModel):
    """One tool call, from raw arguments through validation to execution."""

    call_id: str
    name: str
    raw_arguments: str
    arguments: dict[str, Any] | None = None

    valid: bool = False
    validation_errors: list[str] = Field(default_factory=list)
    executed: bool = False
    #: True when the run fed the validation errors back to the model once.
    repair_attempted: bool = False

    result_summary: str | None = None
    result_data: dict[str, Any] | None = None
    latency_ms: float = 0.0

    @property
    def is_proposal(self) -> bool:
        return self.name in {"propose_diagnosis", "propose_resolution"}


class ModelCall(BaseModel):
    """One attempt against one model."""

    call_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    model: str
    role: ModelRole
    attempt: int = 1
    started_at: datetime
    latency_ms: float = 0.0
    ok: bool = False
    error_code: str | None = None
    error_message: str | None = None
    finish_reason: str | None = None
    tool_call_count: int = 0
    usage: dict[str, Any] | None = None


class AgentRun(BaseModel):
    """The full record of one agent run over one incident."""

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    incident_id: str
    memory_mode: str = "on"
    status: AgentStatus = AgentStatus.COMPLETED
    error: str | None = None

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    latency_ms: float = 0.0

    model_used: str | None = None
    used_fallback: bool = False

    steps: int = 0
    final_text: str | None = None

    model_calls: list[ModelCall] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)

    # Populated from validated proposal tool calls only.
    diagnosis: dict[str, Any] | None = None
    resolution: dict[str, Any] | None = None

    recalled_memory_ids: list[str] = Field(default_factory=list)
    memory_trace_ids: list[str] = Field(default_factory=list)

    def finish(self, status: AgentStatus | None = None, error: str | None = None) -> AgentRun:
        self.finished_at = datetime.now(UTC)
        self.latency_ms = round((self.finished_at - self.started_at).total_seconds() * 1000, 2)
        if status is not None:
            self.status = status
        if error is not None:
            self.error = error
        return self

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.tool_calls]

    @property
    def invalid_tool_calls(self) -> list[ToolCall]:
        return [call for call in self.tool_calls if not call.valid]

    @property
    def model_failures(self) -> list[ModelCall]:
        return [call for call in self.model_calls if not call.ok]
