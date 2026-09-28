"""Trace records for every memory operation.

Every recall/retain attempt produces a :class:`MemoryTrace`, including the
failures. The Memory Inspector reads these; nothing is invented for display.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MemoryOperation(StrEnum):
    CREATE_BANK = "create_bank"
    RETAIN = "retain"
    RECALL = "recall"
    HEALTH = "health"


class MemoryMode(StrEnum):
    ON = "on"
    OFF = "off"
    DEGRADED = "degraded"


class ErrorCode(StrEnum):
    NONE = "none"
    AUTH = "hindsight_auth_error"
    NOT_FOUND = "hindsight_not_found"
    VALIDATION = "hindsight_validation_error"
    UNAVAILABLE = "hindsight_unavailable"
    UNKNOWN = "hindsight_unknown_error"
    MEMORY_OFF = "memory_off"
    DISABLED = "memory_disabled"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_trace_id() -> str:
    return uuid.uuid4().hex


# Provider trace keys that carry large vectors. They are useful for debugging
# but must never be stored, returned to the UI, or logged.
_HEAVY_TRACE_KEYS = {"query_embedding", "embedding", "embeddings", "vector", "vectors"}
_MAX_TRACE_ITEMS = 25


def sanitize_provider_trace(value: Any, *, _depth: int = 0) -> Any:
    """Strip embeddings and bound list sizes in a raw provider trace."""
    if _depth > 6:
        return "<truncated>"
    if isinstance(value, dict):
        return {
            key: sanitize_provider_trace(item, _depth=_depth + 1)
            for key, item in value.items()
            if key not in _HEAVY_TRACE_KEYS
        }
    if isinstance(value, list):
        trimmed = [
            sanitize_provider_trace(item, _depth=_depth + 1)
            for item in value[:_MAX_TRACE_ITEMS]
        ]
        if len(value) > _MAX_TRACE_ITEMS:
            trimmed.append(f"<+{len(value) - _MAX_TRACE_ITEMS} more>")
        return trimmed
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "..."
    return value


class MemoryTrace(BaseModel):
    """Normalized result of one memory-layer call."""

    trace_id: str = Field(default_factory=new_trace_id)
    operation: MemoryOperation
    mode: MemoryMode
    started_at: datetime
    finished_at: datetime
    latency_ms: float
    success: bool
    bank_id: str | None = None
    hit_count: int = 0
    attempts: int = 1
    error_code: ErrorCode = ErrorCode.NONE
    error_message: str | None = None
    degraded: bool = False

    # Recall context, for the Memory Inspector.
    query: str | None = None
    tags: list[str] = Field(default_factory=list)
    min_score: float | None = None
    no_match: bool = False

    # Provider-side trace, sanitized. Never contains embeddings.
    provider_trace: dict[str, Any] | None = None

    @classmethod
    def build(
        cls,
        *,
        operation: MemoryOperation,
        mode: MemoryMode,
        started_at: datetime,
        success: bool,
        bank_id: str | None = None,
        hit_count: int = 0,
        attempts: int = 1,
        error_code: ErrorCode = ErrorCode.NONE,
        error_message: str | None = None,
        degraded: bool = False,
        query: str | None = None,
        tags: list[str] | None = None,
        min_score: float | None = None,
        no_match: bool = False,
        provider_trace: dict[str, Any] | None = None,
    ) -> MemoryTrace:
        finished = utcnow()
        return cls(
            operation=operation,
            mode=mode,
            started_at=started_at,
            finished_at=finished,
            latency_ms=round((finished - started_at).total_seconds() * 1000, 2),
            success=success,
            bank_id=bank_id,
            hit_count=hit_count,
            attempts=attempts,
            error_code=error_code,
            error_message=error_message,
            degraded=degraded,
            query=query,
            tags=tags or [],
            min_score=min_score,
            no_match=no_match,
            provider_trace=sanitize_provider_trace(provider_trace) if provider_trace else None,
        )


class TraceLog:
    """Bounded in-process ring buffer of memory traces, newest last."""

    def __init__(self, capacity: int = 500) -> None:
        self._capacity = capacity
        self._traces: list[MemoryTrace] = []

    def add(self, trace: MemoryTrace) -> MemoryTrace:
        self._traces.append(trace)
        if len(self._traces) > self._capacity:
            del self._traces[: len(self._traces) - self._capacity]
        return trace

    def all(self) -> list[MemoryTrace]:
        return list(self._traces)

    def for_bank(self, bank_id: str) -> list[MemoryTrace]:
        return [t for t in self._traces if t.bank_id == bank_id]

    def clear(self) -> None:
        self._traces.clear()
