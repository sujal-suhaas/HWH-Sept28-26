"""Route definitions.

Handlers are sync ``def`` on purpose: the Hindsight SDK and the Groq SDK are
both synchronous, so FastAPI runs these in its threadpool and the whole stack
stays synchronous. The tradeoff is that a slow agent run occupies a worker
thread for its duration.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src import __version__
from src.agent.loop import AgentLoop
from src.agent.trace import AgentRun
from src.api import lifecycle
from src.api.sqlite_store import SqliteIncidentStore
from src.catalog import Catalog
from src.config import Settings
from src.contracts import (
    ChatRequest,
    FeedbackRequest,
    IncidentListResponse,
    IncidentResponse,
    MemoryModeName,
    OpenIncidentRequest,
    TimelineEntry,
)
from src.memory import MemoryEvent, MemoryStore
from src.memory.trace import MemoryTrace, utcnow

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Accessors
# --------------------------------------------------------------------------
def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _store(request: Request) -> SqliteIncidentStore:
    return request.app.state.incidents


def _catalog(request: Request) -> Catalog:
    return request.app.state.catalog


def _memory_for(request: Request, memory_mode: MemoryModeName) -> MemoryStore:
    if memory_mode is MemoryModeName.ON:
        return request.app.state.memory
    return request.app.state.memory_off


def _agent_loop(request: Request, memory_mode: MemoryModeName) -> AgentLoop:
    llm = request.app.state.llm
    if llm is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "the agent is unavailable: GROQ_API_KEY is not configured, so no model can "
                "be called. Incidents cannot be opened until it is set."
            ),
        )
    return AgentLoop(
        memory=_memory_for(request, memory_mode),
        llm=llm,
        catalog=_catalog(request),
        max_steps=_settings(request).agent_max_steps,
        # The agent does not own the trace log. Passing the sink in is what makes
        # every recall the tools perform visible in the Memory Inspector.
        on_memory_trace=request.app.state.traces.add,
    )


def _require_incident(request: Request, incident_id: str) -> IncidentResponse:
    incident = _store(request).get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"unknown incident '{incident_id}'")
    return incident


def _record(request: Request, trace: MemoryTrace) -> MemoryTrace:
    request.app.state.traces.add(trace)
    return trace


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------
@router.get("/health", tags=["system"])
def health(request: Request) -> dict[str, Any]:
    """Cheap liveness check. Makes no external calls."""
    settings = _settings(request)
    return {
        "status": "ok",
        "version": __version__,
        "memory_mode": settings.memory_mode,
        "bank_id": settings.hindsight_bank_id,
        "model_primary": settings.groq_model_primary,
        "model_fallback": settings.groq_model_fallback,
    }


@router.get("/health/memory", tags=["system"])
def memory_health(request: Request) -> dict[str, Any]:
    """Deep check: does the memory layer actually answer?"""
    store = request.app.state.memory
    trace = _record(request, store.health())
    return trace.model_dump(mode="json")


@router.get("/api/memory/traces", tags=["memory"])
def memory_traces(request: Request, limit: int = 50) -> dict[str, Any]:
    """Recent memory traces, newest last. Backs the Memory Inspector."""
    traces = request.app.state.traces.all()[-limit:]
    return {"count": len(traces), "traces": [t.model_dump(mode="json") for t in traces]}


# --------------------------------------------------------------------------
# Incidents
# --------------------------------------------------------------------------
def _retain_all(
    request: Request, incident: IncidentResponse, memory: MemoryStore, pending: list[MemoryEvent]
) -> IncidentResponse:
    """Retain authoritative events, recording every attempt honestly.

    A failed retain is never presented as a success: it gets its own timeline
    entry naming the error code.
    """
    timeline = list(incident.timeline)
    trace_ids = list(incident.memory_trace_ids)

    for event in pending:
        trace = _record(request, memory.retain(event))
        trace_ids.append(trace.trace_id)
        label = event.event_type.value
        if event.outcome:
            label = f"{label} ({event.outcome.value})"
        if trace.success:
            timeline.append(
                TimelineEntry(
                    actor="system",
                    event="memory_retained",
                    detail=f"{label} retained to memory",
                )
            )
        else:
            timeline.append(
                TimelineEntry(
                    actor="system",
                    event="memory_retain_failed",
                    detail=(
                        f"{label} was NOT retained: {trace.error_code.value} "
                        f"({trace.error_message or 'no detail'})"
                    ),
                )
            )

    if not pending:
        return incident
    return incident.model_copy(
        update={
            "timeline": timeline,
            "memory_trace_ids": trace_ids,
            "updated_at": utcnow(),
        }
    )


@router.post("/alerts", response_model=IncidentResponse, status_code=201, tags=["incidents"])
def open_alert(request: Request, body: OpenIncidentRequest) -> IncidentResponse:
    """Normalize an alert, open an incident, and run the agent over it.

    The incident is created and saved even when the agent run fails, because the
    alert is real and the operator still needs it. The failure is reported in
    ``agent_status`` and in the timeline.
    """
    settings = _settings(request)
    store = _store(request)
    memory_mode = MemoryModeName(body.memory_mode or settings.memory_mode)
    # Resolved before anything is created: a missing model must not leave a
    # half-created incident behind.
    loop = _agent_loop(request, memory_mode)

    incident = lifecycle.new_incident(store.next_incident_id(), body.alert, memory_mode)
    incident = store.save(incident)

    memory = _memory_for(request, memory_mode)
    if memory_mode is MemoryModeName.ON:
        incident = _retain_all(
            request, incident, memory, [lifecycle.incident_open_event(incident)]
        )
    else:
        incident = incident.model_copy(
            update={
                "timeline": [
                    *incident.timeline,
                    TimelineEntry(
                        actor="system",
                        event="memory_off",
                        detail=(
                            "memory_mode=off: Hindsight was not called, so this run has no "
                            "historical context and will write no memory"
                        ),
                    ),
                ]
            }
        )

    incident = lifecycle.begin_diagnosis(incident)
    run = loop.run(body.alert, incident.incident_id)
    return store.save(lifecycle.apply_agent_run(incident, run))


@router.get("/incidents", response_model=IncidentListResponse, tags=["incidents"])
def list_incidents(request: Request, limit: int = 50) -> IncidentListResponse:
    """Newest first, which is the order the alert feed wants."""
    incidents = _store(request).list(limit)
    return IncidentListResponse(count=len(incidents), incidents=incidents)


@router.get("/incidents/{incident_id}", response_model=IncidentResponse, tags=["incidents"])
def get_incident(request: Request, incident_id: str) -> IncidentResponse:
    return _require_incident(request, incident_id)


@router.get("/incidents/{incident_id}/memory-trace", tags=["incidents"])
def incident_memory_trace(request: Request, incident_id: str) -> dict[str, Any]:
    """The memory operations performed for one incident.

    Filtered from the in-process trace log rather than stored on the incident, so
    traces age out of a bounded ring buffer on a long-running server. The
    incident keeps the ids it referenced.
    """
    incident = _require_incident(request, incident_id)
    wanted = set(incident.memory_trace_ids)
    traces = [t for t in request.app.state.traces.all() if t.trace_id in wanted]
    return {
        "incident_id": incident.incident_id,
        "memory_mode": incident.memory_mode.value,
        "count": len(traces),
        "traces": [t.model_dump(mode="json") for t in traces],
    }


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------
def _reply_text(run: AgentRun) -> str:
    if run.final_text and run.final_text.strip():
        return run.final_text.strip()
    return (
        f"The agent run ended with status={run.status.value} and produced no reply"
        + (f": {run.error}" if run.error else ".")
        + " Nothing was concluded about this incident."
    )


@router.post("/chat/{incident_id}", response_model=IncidentResponse, tags=["incidents"])
def chat(request: Request, incident_id: str, body: ChatRequest) -> IncidentResponse:
    """An operator follow-up, answered against the same incident and memory."""
    store = _store(request)
    incident = _require_incident(request, incident_id)
    loop = _agent_loop(request, incident.memory_mode)

    incident = incident.model_copy(
        update={
            "operator": body.operator,
            "timeline": [
                *incident.timeline,
                TimelineEntry(
                    actor="operator", event="chat_message", detail=body.message
                ),
            ],
            "updated_at": utcnow(),
        }
    )

    run = loop.run(
        incident.alert,
        incident.incident_id,
        extra_user_messages=[lifecycle.chat_prompt(incident, body.message)],
    )
    incident = lifecycle.apply_agent_run(incident, run)
    incident = incident.model_copy(
        update={
            "timeline": [
                *incident.timeline,
                TimelineEntry(actor="agent", event="chat_reply", detail=_reply_text(run)),
            ],
            "updated_at": utcnow(),
        }
    )
    return store.save(incident)


# --------------------------------------------------------------------------
# Operator feedback
# --------------------------------------------------------------------------
@router.post(
    "/incidents/{incident_id}/feedback",
    response_model=IncidentResponse,
    tags=["incidents"],
)
def submit_feedback(
    request: Request, incident_id: str, body: FeedbackRequest
) -> IncidentResponse:
    """Record an explicit operator outcome, and only then write durable memory."""
    store = _store(request)
    catalog = _catalog(request)
    incident = _require_incident(request, incident_id)

    errors = lifecycle.validate_feedback(incident, body, catalog)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    pending = lifecycle.feedback_events(incident, body, catalog)
    incident = lifecycle.apply_feedback(incident, body)
    incident = _retain_all(
        request, incident, _memory_for(request, incident.memory_mode), pending
    )
    return store.save(incident)


__all__ = ["router"]
