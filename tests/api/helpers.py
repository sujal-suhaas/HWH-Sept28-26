"""Shared helpers for API tests. No network, no live Hindsight, no live Groq."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from src.agent.groq_client import ModelResponse
from src.agent.trace import AgentRun, AgentStatus, ToolCall
from src.api.app import create_app
from src.config import Settings
from src.contracts import (
    AlertPayload,
    IncidentResponse,
    IncidentState,
    MemoryModeName,
    Proposal,
    Severity,
    TimelineEntry,
)
from src.memory import InMemoryMemoryStore
from tests.agent.fake_llm import call, text_response, tool_response

#: Real ids from data/seed. The API tests run against the real catalog.
CHECKOUT = "checkout-api"
RC_KAFKA_LAG = "RC-001"
RB_LEDGER_CONSUMER_GROUP = "RB-014"


def make_incident(incident_id: str = "INC-9001", **overrides: object) -> IncidentResponse:
    payload: dict[str, object] = {
        "incident_id": incident_id,
        "state": IncidentState.WAITING_FOR_OPERATOR,
        "alert": AlertPayload(
            service=CHECKOUT,
            severity=Severity.P1,
            incident_type="latency",
            title="checkout-api p99 above 2s",
            summary="Ledger confirmation calls are timing out after the deploy.",
            fired_at=datetime(2026, 9, 22, 15, 0, tzinfo=UTC),
        ),
        "timeline": [
            TimelineEntry(actor="pager", event="alert_fired", detail="p99 3.41s"),
        ],
        "proposed_diagnosis": Proposal(
            kind="diagnosis",
            status="proposed",
            content="Kafka consumer lag on payments-ledger.",
            evidence_summary="Latency spiked after the ledger deploy.",
            cited_memory_ids=["INC-2201:RESOLUTION"],
            confidence="high",
        ),
        "memory_mode": MemoryModeName.ON,
    }
    payload.update(overrides)
    return IncidentResponse(**payload)  # type: ignore[arg-type]


def make_run(**overrides: object) -> AgentRun:
    """An agent run record, as the loop would produce it."""
    payload: dict[str, object] = {
        "incident_id": "INC-9001",
        "status": AgentStatus.COMPLETED,
        "tool_calls": [
            ToolCall(
                call_id="call_1",
                name="recall_similar_incidents",
                raw_arguments="{}",
                valid=True,
                executed=True,
                result_summary="2 relevant historical memories for checkout-api",
            ),
            ToolCall(
                call_id="call_2",
                name="propose_diagnosis",
                raw_arguments="{}",
                valid=True,
                executed=True,
                result_summary="Diagnosis proposal recorded and awaiting operator confirmation.",
            ),
        ],
        "diagnosis": Proposal(
            kind="diagnosis",
            status="proposed",
            content="Kafka consumer lag on payments-ledger after the deploy.",
            evidence_summary="Latency spiked right after the ledger deploy.",
            cited_memory_ids=["INC-2201:RESOLUTION"],
            confidence="high",
        ).model_dump(mode="json"),
        "resolution": None,
        "model_used": "openai/gpt-oss-120b",
        "recalled_memory_ids": ["INC-2201:RESOLUTION"],
        "memory_trace_ids": ["trace-1", "trace-2"],
    }
    payload.update(overrides)
    return AgentRun(**payload)  # type: ignore[arg-type]


def alert_body(
    *,
    service: str = CHECKOUT,
    severity: str = "p1",
    incident_type: str = "latency",
    title: str = "checkout-api p99 latency above 2s",
    summary: str = (
        "Checkout latency spiked to p99 3.4s right after the payments-ledger deploy. "
        "Ledger confirmation calls are timing out."
    ),
    memory_mode: str | None = None,
    fired_at: str = "2026-09-22T15:00:00Z",
) -> dict[str, object]:
    body: dict[str, object] = {
        "alert": {
            "service": service,
            "severity": severity,
            "incident_type": incident_type,
            "title": title,
            "summary": summary,
            # Fixed, so two runs of "the same alert" really are the same alert.
            "fired_at": fired_at,
            "error_samples": ["checkout_request_duration_p99 3.41"],
        }
    }
    if memory_mode is not None:
        body["memory_mode"] = memory_mode
    return body


def make_client(
    *,
    mode: str = "on",
    store: object | None = None,
    llm: object | None = None,
    sqlite_path: str | None = None,
) -> TestClient:
    """A TestClient with the memory layer and the model both replaced by fakes."""
    settings = Settings(
        memory_mode=mode,
        hindsight_api_key="test-key",
        hindsight_bank_id="dejaops-test",
        groq_api_key="test-key",
        # Each client gets its own database, so tests cannot see each other's
        # incidents through a shared file.
        sqlite_path=sqlite_path or str(Path(tempfile.mkdtemp()) / "dejaops-test.db"),
    )
    app = create_app(settings)
    if store is not None:
        app.state.memory = store
    elif mode == "on":
        app.state.memory = InMemoryMemoryStore(bank_id=settings.hindsight_bank_id)
    if llm is not None:
        app.state.llm = llm
    return TestClient(app)


def memory_store(**kwargs: object) -> InMemoryMemoryStore:
    return InMemoryMemoryStore(bank_id="dejaops-test", **kwargs)  # type: ignore[arg-type]


def seed_confirmed_resolution(store: InMemoryMemoryStore) -> str:
    """One confirmed checkout-api resolution, tagged as recall scopes it.

    Returns the memory id the agent will be given, so a test can cite it.
    """
    from src.memory import EventType, MemoryEvent, Outcome

    event = MemoryEvent(
        event_type=EventType.RESOLUTION,
        incident_id="INC-2201",
        content=(
            "Resolution for incident INC-2201 (checkout-api, latency): validated fix - "
            "scale the payments-ledger consumer group from 4 to 12 and replay the "
            "affected partition. Verified by the operator. Runbook: RB-014."
        ),
        context="validated resolution",
        service=CHECKOUT,
        severity="p1",
        incident_type="latency",
        environment="prod",
        runbook_id=RB_LEDGER_CONSUMER_GROUP,
        outcome=Outcome.CONFIRMED,
    )
    store.retain(event)
    return f"{event.incident_id}:{event.event_type.value}"


def investigate_then_propose(
    *, cited: str | None = None, runbook_id: str | None = None
) -> list[ModelResponse]:
    """A scripted run: recall, propose a diagnosis, propose a resolution, stop."""
    citations = [cited] if cited else []
    return [
        tool_response(
            call(
                "recall_similar_incidents",
                {"service": CHECKOUT, "symptom": "p99 latency spike after payments-ledger deploy"},
                call_id="call_1",
            )
        ),
        tool_response(
            call(
                "propose_diagnosis",
                {
                    "hypothesis": "Kafka consumer lag on payments-ledger after the deploy.",
                    "confidence": "high",
                    "evidence_summary": "Latency spiked right after the ledger deploy.",
                    "cited_memory_ids": citations,
                },
                call_id="call_2",
            )
        ),
        tool_response(
            call(
                "propose_resolution",
                {
                    "fix": "Scale the consumer group and replay the affected partition.",
                    "evidence_summary": "Matches a validated fix for this signature.",
                    "cited_memory_ids": citations,
                    "runbook_id": runbook_id,
                },
                call_id="call_3",
            )
        ),
        text_response("Diagnosis and fix proposed; awaiting operator confirmation."),
    ]
