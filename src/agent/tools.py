"""Agent tools: definitions, argument validation, and handlers.

Model output is untrusted input. Before any handler runs, a call must pass:

1. the tool name is in the allowlist,
2. the arguments parse as a JSON object,
3. required fields are present and typed correctly,
4. enum values are valid,
5. cited memory ids were actually recalled in this run,
6. a proposed runbook id exists in the catalog,
7. a resolution is not proposed before a diagnosis.

Proposal tools write only proposal state on the run. They cannot create
authoritative memory: nothing in this module retains to Hindsight.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from src.catalog import Catalog
from src.contracts import Proposal
from src.memory import MemoryStore, RecallHit, runbook_tags
from src.memory.trace import MemoryTrace, utcnow

logger = logging.getLogger(__name__)

MAX_RECALL_LIMIT = 10
MAX_RUNBOOK_LIMIT = 5


# --------------------------------------------------------------------------
# Argument models - the source of truth for validation
# --------------------------------------------------------------------------
class RecallSimilarIncidentsArgs(BaseModel):
    service: str = Field(min_length=1, description="Service the incident is on, e.g. checkout-api")
    symptom: str = Field(
        min_length=1,
        max_length=1000,
        description="What is being observed, in the operator's words",
    )
    incident_type: (
        Literal["latency", "error_rate", "saturation", "availability", "data_corruption"] | None
    ) = None
    limit: int = Field(default=5, ge=1, le=MAX_RECALL_LIMIT)


class LookupRunbookArgs(BaseModel):
    suspected_cause: str = Field(
        min_length=1,
        max_length=1000,
        description="The cause being investigated, used as the recall query",
    )
    service: str | None = None
    incident_type: (
        Literal["latency", "error_rate", "saturation", "availability", "data_corruption"] | None
    ) = None
    limit: int = Field(default=3, ge=1, le=MAX_RUNBOOK_LIMIT)


class GetServiceMapArgs(BaseModel):
    service: str | None = Field(
        default=None,
        description="A single service to resolve, or omit for the whole estate",
    )


class ProposeDiagnosisArgs(BaseModel):
    hypothesis: str = Field(
        min_length=1,
        max_length=1000,
        description="The proposed root cause, stated as a concrete cause not a symptom",
    )
    confidence: Literal["low", "medium", "high"]
    evidence_summary: str = Field(
        min_length=1,
        max_length=600,
        description="At most two sentences stating what the evidence shows",
    )
    cited_memory_ids: list[str] = Field(
        default_factory=list,
        description="Memory ids returned by tools in this run that support the hypothesis",
    )
    suspected_root_cause_id: str | None = Field(
        default=None,
        description=(
            "Id of the suspected root cause from the catalog returned by get_service_map, "
            "if one applies. Omit rather than guess."
        ),
    )


class ProposeResolutionArgs(BaseModel):
    fix: str = Field(min_length=1, max_length=1000, description="The proposed fix")
    evidence_summary: str = Field(min_length=1, max_length=600)
    cited_memory_ids: list[str] = Field(default_factory=list)
    runbook_id: str | None = Field(
        default=None,
        description="Id of a validated runbook retrieved in this run, if one applies",
    )


# --------------------------------------------------------------------------
# Tool specs handed to the model
# --------------------------------------------------------------------------
def _spec(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


TOOL_SPECS: list[dict[str, Any]] = [
    _spec(
        "recall_similar_incidents",
        "Recall similar past incidents for a service and symptom. Call this before diagnosing.",
        RecallSimilarIncidentsArgs,
    ),
    _spec(
        "lookup_runbook",
        "Look up validated runbooks and validated fixes for a suspected cause.",
        LookupRunbookArgs,
    ),
    _spec(
        "get_service_map",
        "Get the service catalog, or one service with its dependencies.",
        GetServiceMapArgs,
    ),
    _spec(
        "propose_diagnosis",
        "Propose a diagnosis for an operator to confirm. This does not resolve anything.",
        ProposeDiagnosisArgs,
    ),
    _spec(
        "propose_resolution",
        "Propose a fix for an operator to confirm. Requires a diagnosis first.",
        ProposeResolutionArgs,
    ),
]

TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    "recall_similar_incidents": RecallSimilarIncidentsArgs,
    "lookup_runbook": LookupRunbookArgs,
    "get_service_map": GetServiceMapArgs,
    "propose_diagnosis": ProposeDiagnosisArgs,
    "propose_resolution": ProposeResolutionArgs,
}

PROPOSAL_TOOLS = frozenset({"propose_diagnosis", "propose_resolution"})
READ_TOOLS = frozenset({"recall_similar_incidents", "lookup_runbook", "get_service_map"})
ALLOWED_TOOLS = frozenset(TOOL_ARG_MODELS)


# --------------------------------------------------------------------------
# Execution context
# --------------------------------------------------------------------------
@dataclass
class ToolContext:
    """Per-run state. Proposals accumulate here, never in Hindsight."""

    incident_id: str
    memory: MemoryStore
    catalog: Catalog
    memory_mode: str = "on"

    recalled: list[RecallHit] = field(default_factory=list)
    recalled_ids: set[str] = field(default_factory=set)
    memory_trace_ids: list[str] = field(default_factory=list)
    #: Where to register a trace so the Memory Inspector can show it. The agent
    #: does not own the trace log; the API passes its sink in.
    on_memory_trace: Callable[[MemoryTrace], None] | None = None

    diagnosis: Proposal | None = None
    resolution: Proposal | None = None

    def record_trace(self, trace: MemoryTrace) -> None:
        """Record one memory operation: its id on the run, the trace to the sink.

        Keeping these together is what stops an incident referencing a trace the
        log never received.
        """
        self.memory_trace_ids.append(trace.trace_id)
        if self.on_memory_trace is not None:
            self.on_memory_trace(trace)

    def record_hits(self, hits: list[RecallHit]) -> None:
        for hit in hits:
            if hit.memory_id and hit.memory_id not in self.recalled_ids:
                self.recalled_ids.add(hit.memory_id)
                self.recalled.append(hit)


@dataclass
class ToolOutcome:
    """What a handler produced: a model-facing summary plus structured data."""

    summary: str
    data: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _format_validation_error(error: ValidationError) -> list[str]:
    messages = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        messages.append(f"{location}: {item['msg']}")
    return messages


def validate_tool_call(
    name: str, raw_arguments: str
) -> tuple[BaseModel | None, list[str]]:
    """Validate name, JSON shape, types, and enums. Returns (args, errors)."""
    if name not in ALLOWED_TOOLS:
        allowed = ", ".join(sorted(ALLOWED_TOOLS))
        return None, [f"unknown tool '{name}'. Allowed tools: {allowed}"]

    if raw_arguments is None or not str(raw_arguments).strip():
        return None, ["arguments are empty; a JSON object is required"]

    try:
        parsed = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, [f"arguments are not valid JSON: {exc}"]

    if not isinstance(parsed, dict):
        return None, [f"arguments must be a JSON object, got {type(parsed).__name__}"]

    model = TOOL_ARG_MODELS[name]
    try:
        return model(**parsed), []
    except ValidationError as exc:
        return None, _format_validation_error(exc)


def validate_grounding(
    name: str, args: BaseModel, ctx: ToolContext
) -> list[str]:
    """Guards that need run state: no fabricated citations, no invented runbooks."""
    errors: list[str] = []

    if name in PROPOSAL_TOOLS:
        cited = list(getattr(args, "cited_memory_ids", []) or [])
        if ctx.memory_mode == "off" and cited:
            errors.append(
                "cited_memory_ids must be empty when memory_mode is off; no memory was consulted"
            )
        unknown = [memory_id for memory_id in cited if memory_id not in ctx.recalled_ids]
        if unknown:
            errors.append(
                "cited_memory_ids contains ids that were not returned by any tool in this run: "
                f"{unknown}. Cite only ids you were given."
            )

    if name == "propose_resolution":
        if ctx.diagnosis is None:
            errors.append("propose_resolution requires a diagnosis first: call propose_diagnosis")
        runbook_id = getattr(args, "runbook_id", None)
        if runbook_id and ctx.catalog.runbook(runbook_id) is None:
            errors.append(
                f"runbook_id '{runbook_id}' does not exist in the catalog. Use a runbook id "
                f"returned by lookup_runbook."
            )

    if name == "propose_diagnosis":
        root_cause_id = getattr(args, "suspected_root_cause_id", None)
        if root_cause_id and ctx.catalog.root_cause(root_cause_id) is None:
            errors.append(
                f"suspected_root_cause_id '{root_cause_id}' does not exist in the catalog. "
                f"Use a root cause id from get_service_map, or omit the field."
            )

    return errors


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def _memory_off_summary(tool: str) -> str:
    return (
        f"memory_mode=off: Hindsight was not called by {tool}. "
        f"No historical incidents or runbooks are available for this run."
    )


def _hit_to_dict(hit: RecallHit) -> dict[str, Any]:
    return {
        "memory_id": hit.memory_id,
        "text": hit.text,
        "event_type": hit.event_type,
        "incident_id": hit.incident_id,
        "runbook_id": hit.runbook_id,
        "score": hit.score,
        "attributed": bool(hit.metadata),
    }


#: Outcome memories are one short sentence. A long symptom narrative dilutes
#: their similarity score below the relevance threshold and they get filtered
#: out. Measured against the seeded bank: a 40-word symptom scored 0.057 against
#: the matching resolution, while a 12-word one scored 0.37.
_OUTCOME_QUERY_WORDS = 14


def _outcome_query(service: str, text: str) -> str:
    """A short, service-anchored query for retrieving outcome memories.

    Leading with the service name matters: outcome memories are phrased
    "Incident INC-xxxx involving <service> latency was resolved by ...". Measured
    on the seeded bank, dropping the service name took the same query from a top
    score of 1.05 down to 0.22, right at the relevance threshold.
    """
    return f"{service} {' '.join(text.split()[:_OUTCOME_QUERY_WORDS])}"


def _render_hits(hits: list[RecallHit], catalog: Catalog | None = None) -> str:
    lines = []
    for hit in hits:
        line = f"- [{hit.memory_id}] {hit.text}"
        annotations = []
        if hit.event_type:
            annotations.append(f"event_type={hit.event_type}")
        # The runbook id lives in metadata, not in the text. Without it the model
        # has nothing to put in `runbook_id` and invents one.
        if hit.runbook_id:
            annotations.append(f"runbook_id={hit.runbook_id}")
            # A runbook treats exactly one cause, so the cause id travels with it.
            # Without this the model sees the fix but not the cause it addresses.
            if catalog is not None:
                runbook = catalog.runbook(hit.runbook_id)
                if runbook is not None:
                    annotations.append(f"root_cause_id={runbook.root_cause_id}")
        if hit.incident_id:
            annotations.append(f"incident_id={hit.incident_id}")
        if not hit.metadata:
            # Hindsight's own derived paraphrase: real memory, but no provenance
            # to cite. Say so rather than implying it has an incident id.
            annotations.append("unattributed")
        if annotations:
            line += " (" + ", ".join(annotations) + ")"
        lines.append(line)
    return "\n".join(lines)


def handle_recall_similar_incidents(
    ctx: ToolContext, args: RecallSimilarIncidentsArgs
) -> ToolOutcome:
    if ctx.memory_mode == "off":
        return ToolOutcome(
            summary=_memory_off_summary("recall_similar_incidents"), data={"hits": []}
        )

    # Two scoped recalls rather than one broad one. A single service-scoped
    # recall ranks near-duplicate INCIDENT_OPEN memories above the resolutions
    # and postmortems that actually carry an outcome, so the useful memory never
    # reaches the model.
    history_tags = [f"service:{args.service}", "event_type:incident_open"]
    if args.incident_type:
        history_tags.append(f"incident_type:{args.incident_type}")

    outcome_tags = [f"service:{args.service}", "event_type:resolution"]

    history = ctx.memory.recall(args.symptom, tags=history_tags, limit=args.limit)
    ctx.record_trace(history.trace)
    outcomes = ctx.memory.recall(
        _outcome_query(args.service, args.symptom), tags=outcome_tags, limit=args.limit
    )
    ctx.record_trace(outcomes.trace)

    if not history.trace.success and not outcomes.trace.success:
        return ToolOutcome(
            summary=(
                "Memory service unavailable: recall failed "
                f"({history.trace.error_code.value}). Treat this as having no historical "
                "evidence rather than as having no precedent."
            ),
            data={"hits": [], "degraded": True, "error_code": history.trace.error_code.value},
        )

    hits = _ordered_hits([*history.hits, *outcomes.hits])
    if not hits:
        return ToolOutcome(
            summary=(
                f"No relevant historical incident found for {args.service}"
                + (f" ({args.incident_type})" if args.incident_type else "")
                + ". There is no prior precedent to ground a diagnosis on."
            ),
            data={"hits": [], "no_match": True},
        )

    ctx.record_hits(hits)
    sections = []
    if history.hits:
        sections.append("Similar past incidents:\n" + _render_hits(history.hits, ctx.catalog))
    if outcomes.hits:
        sections.append(
            "Validated outcomes for this service:\n" + _render_hits(outcomes.hits, ctx.catalog)
        )
    return ToolOutcome(
        summary=(
            f"{len(hits)} relevant historical memories for {args.service}:\n\n"
            + "\n\n".join(sections)
        ),
        data={
            "hits": [_hit_to_dict(hit) for hit in hits],
            "similar_incidents": len(history.hits),
            "validated_outcomes": len(outcomes.hits),
        },
    )


def _dedupe_by_memory_id(hits: list[RecallHit]) -> list[RecallHit]:
    """Merge recall results, keeping the first (highest-scoring) copy of each id."""
    seen: set[str] = set()
    merged: list[RecallHit] = []
    for hit in hits:
        if hit.memory_id in seen:
            continue
        seen.add(hit.memory_id)
        merged.append(hit)
    return merged


def _ordered_hits(hits: list[RecallHit]) -> list[RecallHit]:
    """Highest score first, then merge duplicates by memory id."""
    ranked = sorted(hits, key=lambda hit: hit.score or 0.0, reverse=True)
    return _dedupe_by_memory_id(ranked)


def handle_lookup_runbook(ctx: ToolContext, args: LookupRunbookArgs) -> ToolOutcome:
    if ctx.memory_mode == "off":
        return ToolOutcome(summary=_memory_off_summary("lookup_runbook"), data={"runbooks": []})

    collected: list[RecallHit] = []
    degraded = False
    query = _outcome_query(args.service or "", args.suspected_cause)

    # Runbook entries are keyed by root cause and service only; adding an
    # incident type here would match nothing.
    #
    # The threshold is dropped only when the scope is exact, i.e. when a service
    # narrows it to that service's own validated runbooks. Without a service the
    # scope is the whole runbook set, and a similarity threshold is what stops an
    # unrelated runbook being presented as relevant.
    runbook_scope = runbook_tags(args.service)
    runbook_outcome = ctx.memory.recall(
        query,
        tags=runbook_scope,
        limit=args.limit,
        min_score=0.0 if args.service else None,
    )
    ctx.record_trace(runbook_outcome.trace)
    degraded = degraded or not runbook_outcome.trace.success
    collected.extend(runbook_outcome.hits)

    # Validated resolutions carry the actual fix that worked, which is often
    # more specific than the runbook title. This scope is broad, so the
    # relevance threshold does real work here.
    resolution_tags = ["event_type:resolution"]
    if args.service:
        resolution_tags.append(f"service:{args.service}")
    if args.incident_type:
        resolution_tags.append(f"incident_type:{args.incident_type}")
    resolution_outcome = ctx.memory.recall(query, tags=resolution_tags, limit=args.limit)
    ctx.record_trace(resolution_outcome.trace)
    degraded = degraded or not resolution_outcome.trace.success
    collected.extend(resolution_outcome.hits)

    if degraded and not collected:
        return ToolOutcome(
            summary=(
                "Memory service unavailable: runbook lookup failed. "
                "No validated fix available. This is an outage, not an absence of precedent."
            ),
            data={"runbooks": [], "degraded": True},
        )

    deduped: list[RecallHit] = []
    seen: set[str] = set()
    for hit in collected:
        if hit.memory_id in seen:
            continue
        seen.add(hit.memory_id)
        deduped.append(hit)
    deduped = deduped[: args.limit]

    if not deduped:
        return ToolOutcome(
            summary=(
                f"No validated runbook or prior validated fix found for '{args.suspected_cause}'. "
                "Do not cite a runbook id."
            ),
            data={"runbooks": [], "no_match": True},
        )

    ctx.record_hits(deduped)
    has_unscoped_runbook = not args.service and any(
        (hit.event_type or "").lower() == "runbook_entry" for hit in deduped
    )
    return ToolOutcome(
        summary=(
            f"{len(deduped)} validated runbook/fix memories"
            + (f" for {args.service}" if args.service else " (no service filter)")
            + ":\n"
            + _render_hits(deduped, ctx.catalog)
            + (
                "\nNote: these are validated fixes for other services and may not apply here."
                if has_unscoped_runbook
                else ""
            )
        ),
        data={"runbooks": [_hit_to_dict(hit) for hit in deduped]},
    )


def handle_get_service_map(ctx: ToolContext, args: GetServiceMapArgs) -> ToolOutcome:
    data = ctx.catalog.service_map(args.service)
    if args.service is None:
        names = ", ".join(ctx.catalog.service_names())
        return ToolOutcome(
            summary=(
                f"{len(ctx.catalog.services)} services in the {ctx.catalog.company} "
                f"estate: {names}"
            ),
            data=data,
        )
    if not data.get("found"):
        return ToolOutcome(
            summary=(
                f"Service '{args.service}' is not in the catalog. Known services: "
                + ", ".join(ctx.catalog.service_names())
            ),
            data=data,
        )
    service = data["service"]
    dependencies = ", ".join(item["name"] for item in data["dependencies"]) or "none"
    return ToolOutcome(
        summary=(
            f"{service['name']} (tier {service['tier']}, owner {service['owner']}, "
            f"SLO {service['slo']}). Depends on: {dependencies}."
        ),
        data=data,
    )


def handle_propose_diagnosis(ctx: ToolContext, args: ProposeDiagnosisArgs) -> ToolOutcome:
    proposal = Proposal(
        kind="diagnosis",
        status="proposed",
        content=args.hypothesis,
        evidence_summary=args.evidence_summary,
        cited_memory_ids=list(args.cited_memory_ids),
        confidence=args.confidence,
        root_cause_id=args.suspected_root_cause_id,
    )
    updated = ctx.diagnosis is not None
    ctx.diagnosis = proposal
    return ToolOutcome(
        summary=(
            f"Diagnosis proposal {'updated' if updated else 'recorded'} and awaiting operator "
            f"confirmation. It is not confirmed and must not be treated as a root cause. "
            f"suspected_root_cause_id={args.suspected_root_cause_id} "
            f"cited_memory_ids={list(args.cited_memory_ids)}"
        ),
        data={"proposal": proposal.model_dump(mode="json")},
    )


def handle_propose_resolution(ctx: ToolContext, args: ProposeResolutionArgs) -> ToolOutcome:
    proposal = Proposal(
        kind="resolution",
        status="pending_confirmation",
        content=args.fix,
        evidence_summary=args.evidence_summary,
        cited_memory_ids=list(args.cited_memory_ids),
        runbook_id=args.runbook_id,
    )
    updated = ctx.resolution is not None
    ctx.resolution = proposal
    return ToolOutcome(
        summary=(
            f"Resolution proposal {'updated' if updated else 'recorded'} and pending operator "
            f"confirmation. The incident is NOT resolved and the fix is NOT verified. "
            f"runbook_id={args.runbook_id}"
        ),
        data={"proposal": proposal.model_dump(mode="json")},
    )


TOOL_HANDLERS: dict[str, Callable[[ToolContext, Any], ToolOutcome]] = {
    "recall_similar_incidents": handle_recall_similar_incidents,
    "lookup_runbook": handle_lookup_runbook,
    "get_service_map": handle_get_service_map,
    "propose_diagnosis": handle_propose_diagnosis,
    "propose_resolution": handle_propose_resolution,
}


def execute_tool_call(name: str, args: BaseModel, ctx: ToolContext) -> ToolOutcome:
    """Run a handler. Only allowlisted tools can reach here."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"no handler registered for tool '{name}'")
    started = time.perf_counter()
    outcome = handler(ctx, args)
    logger.debug("tool %s completed in %.1fms", name, (time.perf_counter() - started) * 1000)
    return outcome


__all__ = [
    "ALLOWED_TOOLS",
    "PROPOSAL_TOOLS",
    "READ_TOOLS",
    "TOOL_ARG_MODELS",
    "TOOL_HANDLERS",
    "TOOL_SPECS",
    "GetServiceMapArgs",
    "LookupRunbookArgs",
    "ProposeDiagnosisArgs",
    "ProposeResolutionArgs",
    "RecallSimilarIncidentsArgs",
    "ToolContext",
    "ToolOutcome",
    "execute_tool_call",
    "runbook_tags",
    "utcnow",
    "validate_grounding",
    "validate_tool_call",
]
