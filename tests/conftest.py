"""Shared test helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.memory.schema import EventType, MemoryEvent, Outcome


def make_event(**overrides: Any) -> MemoryEvent:
    payload: dict[str, Any] = {
        "event_type": EventType.POSTMORTEM,
        "incident_id": "INC-1001",
        "content": (
            "checkout-api latency spike after payments-ledger deploy; "
            "root cause Kafka consumer lag"
        ),
        "context": "postmortem",
        "service": "checkout-api",
        "severity": "p1",
        "incident_type": "latency",
        "environment": "prod",
        "runbook_id": "RB-014",
        "outcome": Outcome.CONFIRMED,
    }
    payload.update(overrides)
    return MemoryEvent(**payload)


def recall_item(
    *,
    memory_id: str = "m-1",
    text: str = "checkout-api latency after payments-ledger deploy",
    final_score: float = 0.91,
    metadata: dict[str, str] | None = None,
    tags: list[str] | None = None,
) -> SimpleNamespace:
    """Mimic a hindsight_client_api RecallResult closely enough to normalize."""
    return SimpleNamespace(
        id=memory_id,
        text=text,
        type="world",
        entities=None,
        context="postmortem",
        occurred_start="2026-08-01T00:00:00Z",
        occurred_end=None,
        mentioned_at=None,
        document_id="INC-1001:postmortem",
        metadata=(
            metadata
            if metadata is not None
            else {"incident_id": "INC-1001", "event_type": "POSTMORTEM"}
        ),
        chunk_id=None,
        tags=tags if tags is not None else ["service:checkout-api"],
        source_fact_ids=None,
        scores=SimpleNamespace(final=final_score, reranker=0.5, semantic=0.7, keyword=0.4),
        attachments=None,
    )
