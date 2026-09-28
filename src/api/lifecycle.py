"""The incident lifecycle: state transitions and what becomes durable memory.

This module is where AGENTS.md §4's source-of-truth rule is enforced. Two things
are kept deliberately separate:

* the model **proposes**, and its proposal is written to the timeline as
  ``proposed`` or ``pending_confirmation``;
* the backend **decides**, and only an explicit operator outcome turns a
  proposal into a ``DIAGNOSIS``, ``RESOLUTION``, or ``OPERATOR_CORRECTION``
  memory.

Nothing here trusts a model's claim that something was resolved. The functions
that decide which memory events to write are pure, so the rule can be tested
without SQLite, Hindsight, or an LLM.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.catalog import Catalog
from src.contracts import (
    AlertPayload,
    FeedbackRequest,
    FeedbackType,
    IncidentResponse,
    IncidentState,
    MemoryModeName,
    Proposal,
    TimelineEntry,
)
from src.memory import MemoryEvent, Outcome, events
from src.memory.trace import utcnow

logger = logging.getLogger(__name__)

MAX_TOOL_DETAIL_CHARS = 400

#: Where each operator outcome leaves the incident. An incident that is already
#: RESOLVED accepts no further outcome changes.
FEEDBACK_STATE: dict[FeedbackType, IncidentState] = {
    FeedbackType.DIAGNOSIS_CONFIRMED: IncidentState.RESOLVING,
    FeedbackType.DIAGNOSIS_REJECTED: IncidentState.DIAGNOSING,
    FeedbackType.OPERATOR_CORRECTION: IncidentState.RESOLVING,
    FeedbackType.RESOLUTION_CONFIRMED: IncidentState.RESOLVED,
    FeedbackType.RESOLUTION_FAILED: IncidentState.RESOLVING,
    FeedbackType.INCONCLUSIVE: IncidentState.INCONCLUSIVE,
}

DIAGNOSIS_OUTCOME: dict[FeedbackType, Outcome] = {
    FeedbackType.DIAGNOSIS_CONFIRMED: Outcome.CONFIRMED,
    FeedbackType.DIAGNOSIS_REJECTED: Outcome.REJECTED,
    FeedbackType.INCONCLUSIVE: Outcome.INCONCLUSIVE,
}

#: Human-readable record of what the operator said, for the timeline.
FEEDBACK_TIMELINE: dict[FeedbackType, str] = {
    FeedbackType.DIAGNOSIS_CONFIRMED: "diagnosis confirmed by operator",
    FeedbackType.DIAGNOSIS_REJECTED: "diagnosis rejected by operator",
    FeedbackType.OPERATOR_CORRECTION: "operator corrected the agent",
    FeedbackType.RESOLUTION_CONFIRMED: "resolution confirmed by operator",
    FeedbackType.RESOLUTION_FAILED: "resolution reported as failed by operator",
    FeedbackType.INCONCLUSIVE: "operator marked the incident inconclusive",
}


# --------------------------------------------------------------------------
# Opening an incident
# --------------------------------------------------------------------------
def new_incident(
    incident_id: str, alert: AlertPayload, memory_mode: MemoryModeName
) -> IncidentResponse:
    """A fresh incident in OPEN state. No proposal, no outcome, no memory yet."""
    return IncidentResponse(
        incident_id=incident_id,
        state=IncidentState.OPEN,
        alert=alert,
        timeline=[
            TimelineEntry(
                at=alert.fired_at,
                actor="pager",
                event="alert_fired",
                detail=f"{alert.service} {alert.incident_type.value}: {alert.title}",
            ),
            TimelineEntry(
                actor="system",
                event="incident_opened",
                detail=f"normalized alert received from {alert.source}",
            ),
        ],
        memory_mode=memory_mode,
    )


def begin_diagnosis(incident: IncidentResponse) -> IncidentResponse:
    """Move an incident into DIAGNOSING, before the agent runs.

    Doing this first is what makes a failed run read correctly: the incident is
    visibly in progress with a failed attempt, rather than looking untouched.
    """
    return incident.model_copy(
        update={
            "state": IncidentState.DIAGNOSING,
            "timeline": [
                *incident.timeline,
                TimelineEntry(
                    actor="system", event="diagnosing", detail="agent run started"
                ),
            ],
            "updated_at": utcnow(),
        }
    )


def incident_open_event(incident: IncidentResponse) -> MemoryEvent:
    """The one memory retained automatically, with no operator involved."""
    alert = incident.alert
    return events.incident_open_event(
        incident_id=incident.incident_id,
        service=alert.service,
        severity=alert.severity.value,
        incident_type=alert.incident_type.value,
        title=alert.title,
        summary=alert.summary,
        signals=alert.error_samples,
        environment=alert.environment,
        timestamp=alert.fired_at,
    )


# --------------------------------------------------------------------------
# Applying an agent run
# --------------------------------------------------------------------------
def _tool_detail(call) -> str:  # noqa: ANN001 - src.agent.trace.ToolCall
    if call.validation_errors:
        detail = "rejected: " + "; ".join(call.validation_errors)
    elif call.result_summary:
        detail = call.result_summary
    else:
        detail = "no result"
    detail = " ".join(detail.split())
    if len(detail) > MAX_TOOL_DETAIL_CHARS:
        return detail[:MAX_TOOL_DETAIL_CHARS] + "…"
    return detail


def apply_agent_run(incident: IncidentResponse, run) -> IncidentResponse:  # noqa: ANN001
    """Fold an agent run into the incident.

    Proposals are recorded as proposals. An existing proposal is never cleared by
    a later run that proposed nothing, so a follow-up chat turn cannot erase a
    diagnosis the operator is still looking at.
    """
    timeline = list(incident.timeline)
    for call in run.tool_calls:
        timeline.append(
            TimelineEntry(actor="agent", event=call.name, detail=_tool_detail(call))
        )

    diagnosis = incident.proposed_diagnosis
    if run.diagnosis:
        diagnosis = Proposal.model_validate(run.diagnosis)
    resolution = incident.proposed_resolution
    if run.resolution:
        resolution = Proposal.model_validate(run.resolution)

    completed = run.status.value == "completed"
    if not completed:
        timeline.append(
            TimelineEntry(
                actor="system",
                event="agent_run_failed",
                detail=f"run ended with status={run.status.value}: {run.error or 'no detail'}",
            )
        )
        state = incident.state
    else:
        cited = len(diagnosis.cited_memory_ids) if diagnosis else 0
        rejected = sum(1 for call in run.tool_calls if not call.valid)
        detail = (
            f"status=completed, {len(run.tool_calls)} tool call(s), "
            f"{len(run.recalled_memory_ids)} memory item(s) recalled, "
            f"{cited} cited"
        )
        if rejected:
            # A model that gave up after a rejected call still "completed". Say
            # how many calls were refused so the count is not read as success.
            detail += f", {rejected} rejected"
        timeline.append(
            TimelineEntry(actor="agent", event="agent_run_finished", detail=detail)
        )
        if diagnosis is not None:
            state = IncidentState.WAITING_FOR_OPERATOR
        elif incident.state in {IncidentState.OPEN, IncidentState.DIAGNOSING}:
            state = IncidentState.DIAGNOSING
        else:
            state = incident.state

    return incident.model_copy(
        update={
            "state": state,
            "timeline": timeline,
            "proposed_diagnosis": diagnosis,
            "proposed_resolution": resolution,
            "model_used": run.model_used or incident.model_used,
            "agent_status": run.status.value,
            "memory_trace_ids": [*incident.memory_trace_ids, *run.memory_trace_ids],
            "tool_trace_ids": [
                *incident.tool_trace_ids,
                *(call.call_id for call in run.tool_calls),
            ],
            "updated_at": utcnow(),
        }
    )


def chat_prompt(incident: IncidentResponse, message: str) -> str:
    """The user turn for a chat follow-up.

    Includes the current proposal state so the agent answers about the incident
    as it stands rather than starting over.
    """
    lines = [f"Operator follow-up: {message}", "", "Current incident state:"]
    lines.append(f"- state: {incident.state.value}")
    lines.append(f"- service: {incident.alert.service} ({incident.alert.incident_type.value})")
    if incident.proposed_diagnosis:
        lines.append(f"- proposed diagnosis (unconfirmed): {incident.proposed_diagnosis.content}")
    else:
        lines.append("- no diagnosis proposed yet")
    if incident.proposed_resolution:
        lines.append(
            f"- proposed resolution (unconfirmed): {incident.proposed_resolution.content}"
        )
    else:
        lines.append("- no resolution proposed yet")
    lines.append("")
    lines.append("Answer the operator's follow-up concisely.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Operator feedback
# --------------------------------------------------------------------------
def validate_feedback(
    incident: IncidentResponse, feedback: FeedbackRequest, catalog: Catalog
) -> list[str]:
    """Reject feedback that would create a dangling reference or a contradiction."""
    errors: list[str] = []

    if feedback.root_cause_id and catalog.root_cause(feedback.root_cause_id) is None:
        errors.append(
            f"root_cause_id '{feedback.root_cause_id}' does not exist in the catalog"
        )
    if (
        feedback.validated_runbook_id
        and catalog.runbook(feedback.validated_runbook_id) is None
    ):
        errors.append(
            f"validated_runbook_id '{feedback.validated_runbook_id}' does not exist "
            "in the catalog"
        )
    if incident.state is IncidentState.RESOLVED:
        errors.append(
            "incident is already RESOLVED; open a new incident rather than "
            "changing a closed outcome"
        )

    return errors


def feedback_events(
    incident: IncidentResponse, feedback: FeedbackRequest, catalog: Catalog
) -> list[MemoryEvent]:
    """The durable memories an operator outcome creates. Pure function.

    Returns an empty list for a ``memory_mode=off`` incident: the OFF half of the
    ON/OFF comparison must not write memory, or the comparison stops meaning
    anything.
    """
    if incident.memory_mode is MemoryModeName.OFF:
        return []

    alert = incident.alert
    common: dict[str, object] = {
        "incident_id": incident.incident_id,
        "service": alert.service,
        "severity": alert.severity.value,
        "incident_type": alert.incident_type.value,
        "environment": alert.environment,
    }
    hypothesis = (
        incident.proposed_diagnosis.content if incident.proposed_diagnosis else None
    )

    if feedback.feedback_type in DIAGNOSIS_OUTCOME:
        return [
            events.diagnosis_event(
                **common,  # type: ignore[arg-type]
                outcome=DIAGNOSIS_OUTCOME[feedback.feedback_type],
                hypothesis=hypothesis or "(the agent proposed no diagnosis)",
                confirmed_root_cause=feedback.root_cause_id,
            )
        ]

    if feedback.feedback_type is FeedbackType.OPERATOR_CORRECTION:
        return [
            events.operator_correction_event(
                **common,  # type: ignore[arg-type]
                corrected_root_cause=feedback.corrected_root_cause or "(unspecified)",
                agent_hypothesis=hypothesis,
                validated_fix=feedback.validated_fix,
                runbook_id=feedback.validated_runbook_id,
            )
        ]

    if feedback.feedback_type is FeedbackType.RESOLUTION_CONFIRMED:
        written = [
            events.resolution_event(
                **common,  # type: ignore[arg-type]
                fix=feedback.validated_fix or "(unspecified)",
                runbook_id=feedback.validated_runbook_id,
                time_to_resolve_minutes=_minutes_since(incident.created_at),
                outcome=Outcome.CONFIRMED,
            )
        ]
        # A confirmed resolution may promote its runbook. The document id is per
        # (runbook, service), so promoting one that seeding already covered
        # rewrites the same memory instead of adding a duplicate.
        runbook = (
            catalog.runbook(feedback.validated_runbook_id)
            if feedback.validated_runbook_id
            else None
        )
        if runbook is not None:
            written.append(
                events.runbook_entry_event(
                    runbook_id=runbook.id,
                    root_cause_id=runbook.root_cause_id,
                    title=runbook.title,
                    steps=runbook.steps,
                    service=alert.service,
                )
            )
        else:
            logger.info(
                "incident %s: resolution confirmed without a validated runbook, "
                "so nothing is promoted to RUNBOOK_ENTRY",
                incident.incident_id,
            )
        return written

    if feedback.feedback_type is FeedbackType.RESOLUTION_FAILED:
        # Record the fix that did not work so it is not proposed again. Never a
        # validated runbook. The operator's note is the last resort, because a
        # failure with no description at all is not worth remembering.
        failed_fix = feedback.validated_fix or (
            incident.proposed_resolution.content if incident.proposed_resolution else None
        )
        failed_fix = failed_fix or feedback.note
        if failed_fix is None:
            logger.info(
                "incident %s: RESOLUTION_FAILED carried no fix and no note, so there is "
                "nothing durable to retain",
                incident.incident_id,
            )
            return []
        return [
            events.resolution_event(
                **common,  # type: ignore[arg-type]
                fix=failed_fix,
                outcome=Outcome.FAILED,
            )
        ]

    logger.info(
        "incident %s: feedback %s produces no durable memory",
        incident.incident_id,
        feedback.feedback_type.value,
    )
    return []


def _minutes_since(moment: datetime) -> int:
    delta = utcnow() - moment.astimezone(UTC)
    return max(0, int(delta.total_seconds() // 60))


def apply_feedback(
    incident: IncidentResponse, feedback: FeedbackRequest
) -> IncidentResponse:
    """Move the incident to its post-feedback state and record it in the timeline."""
    timeline = list(incident.timeline)
    detail = FEEDBACK_TIMELINE[feedback.feedback_type]
    if feedback.note:
        detail = f"{detail}: {feedback.note}"
    timeline.append(
        TimelineEntry(actor="operator", event=feedback.feedback_type.value, detail=detail)
    )

    if incident.memory_mode is MemoryModeName.OFF:
        timeline.append(
            TimelineEntry(
                actor="system",
                event="memory_off",
                detail=(
                    "memory_mode=off: this outcome was recorded on the incident but "
                    "written to no memory"
                ),
            )
        )

    updates: dict[str, object] = {
        "state": FEEDBACK_STATE[feedback.feedback_type],
        "timeline": timeline,
        "operator_outcome": feedback.feedback_type,
        "operator": feedback.operator,
        "updated_at": utcnow(),
    }
    if feedback.root_cause_id:
        updates["root_cause_id"] = feedback.root_cause_id
    if feedback.validated_runbook_id:
        updates["validated_runbook_id"] = feedback.validated_runbook_id
    return incident.model_copy(update=updates)


__all__ = [
    "DIAGNOSIS_OUTCOME",
    "FEEDBACK_STATE",
    "FEEDBACK_TIMELINE",
    "apply_agent_run",
    "apply_feedback",
    "begin_diagnosis",
    "chat_prompt",
    "feedback_events",
    "incident_open_event",
    "new_incident",
    "validate_feedback",
]
