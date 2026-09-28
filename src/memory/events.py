"""Construction of durable memory events, shared by every writer.

There are two writers of operational memory: the seed loader, which replays the
NimbusPay history, and the live operator-feedback path. Both must phrase a given
event identically. If a live ``INCIDENT_OPEN`` read differently from a seeded
one, a live alert would stop matching the history it is supposed to recall, and
the memory would quietly get worse rather than fail loudly.

So the content formats live here, once, and take primitives only: this module
imports neither the catalog nor the HTTP contract, which keeps the memory layer
a leaf that everything else depends on.

Nothing here decides *whether* to retain. That decision belongs to the backend
lifecycle, never to the model.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from src.memory.schema import EventType, MemoryEvent, Outcome

DEFAULT_CONTEXT: dict[EventType, str] = {
    EventType.INCIDENT_OPEN: "incident open",
    EventType.DIAGNOSIS: "diagnosis outcome",
    EventType.RESOLUTION: "validated resolution",
    EventType.OPERATOR_CORRECTION: "operator correction",
    EventType.RUNBOOK_ENTRY: "validated runbook",
    EventType.POSTMORTEM: "postmortem",
}


# --------------------------------------------------------------------------
# Content formats. These strings are the recall surface: change one and the
# embedding changes with it, so treat them as data, not prose.
# --------------------------------------------------------------------------
def incident_open_content(
    *,
    incident_id: str,
    service: str,
    severity: str,
    incident_type: str,
    title: str,
    summary: str,
    signals: Iterable[str] = (),
) -> str:
    content = (
        f"Incident {incident_id} opened on {service} "
        f"(severity {severity}, {incident_type}). "
        f"Alert: {title}. Summary: {summary}"
    )
    joined = "; ".join(item for item in signals if item)
    if joined:
        content += f" Initial signals: {joined}"
    return content


def diagnosis_content(
    *,
    incident_id: str,
    service: str,
    incident_type: str,
    outcome: str,
    hypothesis: str,
    confirmed_root_cause: str | None = None,
) -> str:
    content = (
        f"Diagnosis for incident {incident_id} ({service}, "
        f"{incident_type}) was {outcome}. "
        f"Agent hypothesis: {hypothesis}."
    )
    if outcome == Outcome.CONFIRMED.value and confirmed_root_cause:
        content += f" Confirmed root cause: {confirmed_root_cause}."
    elif outcome == Outcome.REJECTED.value:
        content += " The proposed hypothesis was rejected by the operator."
    return content


def resolution_content(
    *,
    incident_id: str,
    service: str,
    incident_type: str,
    fix: str,
    runbook_id: str | None = None,
    time_to_resolve_minutes: int | None = None,
    verified: bool = True,
) -> str:
    content = (
        f"Resolution for incident {incident_id} ({service}, "
        f"{incident_type}): validated fix - {fix}. "
    )
    content += (
        "Verified by the operator. " if verified else "NOT verified by the operator. "
    )
    if time_to_resolve_minutes is not None:
        content += f"Time to resolve: {time_to_resolve_minutes} minutes."
    if runbook_id:
        content += f" Runbook: {runbook_id}."
    return content


def operator_correction_content(
    *,
    incident_id: str,
    service: str,
    incident_type: str,
    corrected_root_cause: str,
    agent_hypothesis: str | None = None,
    validated_fix: str | None = None,
    runbook_id: str | None = None,
) -> str:
    content = (
        f"Operator correction for incident {incident_id} ({service}, {incident_type}): "
        f"the agent was wrong. Actual root cause: {corrected_root_cause}."
    )
    if agent_hypothesis:
        content += f" The agent had proposed: {agent_hypothesis}."
    if validated_fix:
        content += f" Validated fix: {validated_fix}."
    if runbook_id:
        content += f" Runbook: {runbook_id}."
    return content


def runbook_content(
    *, runbook_id: str, root_cause_id: str, title: str, steps: Sequence[str]
) -> str:
    numbered = " ".join(f"{index + 1}) {step}" for index, step in enumerate(steps))
    return (
        f"Validated runbook {runbook_id} for root cause {root_cause_id}: "
        f"{title}. Steps: {numbered}"
    )


def postmortem_content(
    *,
    postmortem_id: str,
    incident_id: str,
    service: str,
    root_cause_id: str,
    root_cause: str,
    detail: str,
    contributing_factors: Sequence[str] = (),
    prevention: Sequence[str] = (),
) -> str:
    return (
        f"Postmortem {postmortem_id} for incident {incident_id} "
        f"({service}): root cause {root_cause_id} - "
        f"{root_cause}. {detail} "
        f"Contributing factors: {'; '.join(contributing_factors)}. "
        f"Prevention: {'; '.join(prevention)}."
    )


# --------------------------------------------------------------------------
# Event constructors. One per category, primitives in, MemoryEvent out.
# --------------------------------------------------------------------------
def incident_open_event(
    *,
    incident_id: str,
    service: str,
    severity: str,
    incident_type: str,
    title: str,
    summary: str,
    signals: Iterable[str] = (),
    environment: str = "prod",
    timestamp: datetime | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_type=EventType.INCIDENT_OPEN,
        incident_id=incident_id,
        content=incident_open_content(
            incident_id=incident_id,
            service=service,
            severity=severity,
            incident_type=incident_type,
            title=title,
            summary=summary,
            signals=signals,
        ),
        context=DEFAULT_CONTEXT[EventType.INCIDENT_OPEN],
        service=service,
        severity=severity,
        incident_type=incident_type,
        environment=environment,
        timestamp=timestamp or _now(),
    )


def diagnosis_event(
    *,
    incident_id: str,
    service: str,
    severity: str,
    incident_type: str,
    outcome: Outcome,
    hypothesis: str,
    confirmed_root_cause: str | None = None,
    environment: str = "prod",
    timestamp: datetime | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_type=EventType.DIAGNOSIS,
        incident_id=incident_id,
        content=diagnosis_content(
            incident_id=incident_id,
            service=service,
            incident_type=incident_type,
            outcome=outcome.value,
            hypothesis=hypothesis,
            confirmed_root_cause=confirmed_root_cause,
        ),
        context=DEFAULT_CONTEXT[EventType.DIAGNOSIS],
        service=service,
        severity=severity,
        incident_type=incident_type,
        environment=environment,
        outcome=outcome,
        timestamp=timestamp or _now(),
    )


def resolution_event(
    *,
    incident_id: str,
    service: str,
    severity: str,
    incident_type: str,
    fix: str,
    runbook_id: str | None = None,
    time_to_resolve_minutes: int | None = None,
    outcome: Outcome = Outcome.CONFIRMED,
    environment: str = "prod",
    timestamp: datetime | None = None,
) -> MemoryEvent:
    """A resolution memory.

    ``outcome=FAILED`` records that a fix did not work. It is retained so the
    same fix is not proposed again, and it is never a validated runbook.
    """
    return MemoryEvent(
        event_type=EventType.RESOLUTION,
        incident_id=incident_id,
        content=resolution_content(
            incident_id=incident_id,
            service=service,
            incident_type=incident_type,
            fix=fix,
            runbook_id=runbook_id,
            time_to_resolve_minutes=time_to_resolve_minutes,
            verified=outcome is Outcome.CONFIRMED,
        ),
        context=DEFAULT_CONTEXT[EventType.RESOLUTION],
        service=service,
        severity=severity,
        incident_type=incident_type,
        environment=environment,
        outcome=outcome,
        runbook_id=runbook_id,
        timestamp=timestamp or _now(),
    )


def operator_correction_event(
    *,
    incident_id: str,
    service: str,
    severity: str,
    incident_type: str,
    corrected_root_cause: str,
    agent_hypothesis: str | None = None,
    validated_fix: str | None = None,
    runbook_id: str | None = None,
    environment: str = "prod",
    timestamp: datetime | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_type=EventType.OPERATOR_CORRECTION,
        incident_id=incident_id,
        content=operator_correction_content(
            incident_id=incident_id,
            service=service,
            incident_type=incident_type,
            corrected_root_cause=corrected_root_cause,
            agent_hypothesis=agent_hypothesis,
            validated_fix=validated_fix,
            runbook_id=runbook_id,
        ),
        context=DEFAULT_CONTEXT[EventType.OPERATOR_CORRECTION],
        service=service,
        severity=severity,
        incident_type=incident_type,
        environment=environment,
        outcome=Outcome.CONFIRMED,
        runbook_id=runbook_id,
        timestamp=timestamp or _now(),
    )


def runbook_entry_document_id(runbook_id: str, service: str | None) -> str:
    """One document per (runbook, service) pair.

    A runbook validated for several services needs a memory per service, because
    a lookup scoped to one service must find it. This exact id is what makes
    re-promoting an already-seeded pair a no-op instead of a duplicate.
    """
    return f"RUNBOOK-{runbook_id}:{service or 'global'}:runbook_entry"


def runbook_entry_event(
    *,
    runbook_id: str,
    root_cause_id: str,
    title: str,
    steps: Sequence[str],
    service: str | None = None,
    promoted_at: datetime | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_type=EventType.RUNBOOK_ENTRY,
        incident_id=f"RUNBOOK-{runbook_id}",
        content=runbook_content(
            runbook_id=runbook_id, root_cause_id=root_cause_id, title=title, steps=steps
        ),
        context=DEFAULT_CONTEXT[EventType.RUNBOOK_ENTRY],
        service=service,
        # Deliberately no incident_type: a runbook is keyed by root cause and
        # service. Tagging it with an incident type would make every runbook
        # lookup scoped by incident type match nothing.
        incident_type=None,
        runbook_id=runbook_id,
        outcome=Outcome.CONFIRMED,
        timestamp=promoted_at or _now(),
        document_id=runbook_entry_document_id(runbook_id, service),
    )


def _now() -> datetime:
    from src.memory.trace import utcnow

    return utcnow()


def postmortem_event(
    *,
    postmortem_id: str,
    incident_id: str,
    service: str,
    root_cause_id: str,
    root_cause: str,
    detail: str,
    contributing_factors: Sequence[str] = (),
    prevention: Sequence[str] = (),
    severity: str | None = None,
    incident_type: str | None = None,
    environment: str = "prod",
    timestamp: datetime | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_type=EventType.POSTMORTEM,
        incident_id=incident_id,
        content=postmortem_content(
            postmortem_id=postmortem_id,
            incident_id=incident_id,
            service=service,
            root_cause_id=root_cause_id,
            root_cause=root_cause,
            detail=detail,
            contributing_factors=contributing_factors,
            prevention=prevention,
        ),
        context=DEFAULT_CONTEXT[EventType.POSTMORTEM],
        service=service,
        severity=severity,
        incident_type=incident_type,
        environment=environment,
        timestamp=timestamp or _now(),
    )


__all__ = [
    "DEFAULT_CONTEXT",
    "diagnosis_content",
    "diagnosis_event",
    "incident_open_content",
    "incident_open_event",
    "operator_correction_content",
    "operator_correction_event",
    "postmortem_content",
    "postmortem_event",
    "resolution_content",
    "resolution_event",
    "runbook_content",
    "runbook_entry_document_id",
    "runbook_entry_event",
]
