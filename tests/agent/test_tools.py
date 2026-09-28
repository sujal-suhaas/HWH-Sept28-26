"""Tool definition, validation, and handler tests."""

from __future__ import annotations

import json

import pytest

from src.agent.tools import (
    ALLOWED_TOOLS,
    PROPOSAL_TOOLS,
    READ_TOOLS,
    TOOL_ARG_MODELS,
    TOOL_SPECS,
    GetServiceMapArgs,
    LookupRunbookArgs,
    ProposeDiagnosisArgs,
    ProposeResolutionArgs,
    RecallSimilarIncidentsArgs,
    ToolContext,
    execute_tool_call,
    validate_grounding,
    validate_tool_call,
)
from src.memory.disabled import DisabledMemoryStore
from src.memory.interface import RecallHit
from src.memory.schema import EventType, MemoryEvent, Outcome
from tests.agent.conftest import seed_lag_incident

# --------------------------------------------------------------------------
# Specs
# --------------------------------------------------------------------------


def test_every_spec_has_an_arg_model_and_the_reverse() -> None:
    spec_names = {spec["function"]["name"] for spec in TOOL_SPECS}
    assert spec_names == set(TOOL_ARG_MODELS) == set(ALLOWED_TOOLS)


def test_specs_are_well_formed_json_schema() -> None:
    for spec in TOOL_SPECS:
        assert spec["type"] == "function"
        function = spec["function"]
        assert function["description"]
        schema = function["parameters"]
        assert schema["type"] == "object"
        # Every argument must be serialisable by the provider.
        json.dumps(schema)


def test_read_and_proposal_tools_partition_the_allowlist() -> None:
    assert READ_TOOLS | PROPOSAL_TOOLS == ALLOWED_TOOLS
    assert not (READ_TOOLS & PROPOSAL_TOOLS)


# --------------------------------------------------------------------------
# validate_tool_call
# --------------------------------------------------------------------------
def test_unknown_tool_is_rejected_with_the_allowlist() -> None:
    args, errors = validate_tool_call("drop_database", "{}")
    assert args is None
    assert len(errors) == 1
    assert "unknown tool 'drop_database'" in errors[0]
    assert "recall_similar_incidents" in errors[0]


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("", "arguments are empty"),
        ("   ", "arguments are empty"),
        ("{not json", "not valid JSON"),
        ("[1, 2, 3]", "must be a JSON object"),
        ('"a string"', "must be a JSON object"),
        ("42", "must be a JSON object"),
    ],
)
def test_malformed_arguments_are_rejected(raw: str, fragment: str) -> None:
    args, errors = validate_tool_call("recall_similar_incidents", raw)
    assert args is None
    assert any(fragment in error for error in errors)


def test_missing_required_field_is_reported_by_name() -> None:
    args, errors = validate_tool_call(
        "recall_similar_incidents", json.dumps({"service": "checkout-api"})
    )
    assert args is None
    assert any("symptom" in error for error in errors)


def test_blank_required_field_is_rejected() -> None:
    args, errors = validate_tool_call(
        "recall_similar_incidents", json.dumps({"service": "", "symptom": "latency"})
    )
    assert args is None
    assert any("service" in error for error in errors)


def test_invalid_enum_value_is_rejected() -> None:
    args, errors = validate_tool_call(
        "recall_similar_incidents",
        json.dumps({"service": "checkout-api", "symptom": "x", "incident_type": "vibes"}),
    )
    assert args is None
    assert any("incident_type" in error for error in errors)


def test_invalid_confidence_is_rejected() -> None:
    args, errors = validate_tool_call(
        "propose_diagnosis",
        json.dumps(
            {
                "hypothesis": "consumer lag",
                "confidence": "certain",
                "evidence_summary": "lag observed",
            }
        ),
    )
    assert args is None
    assert any("confidence" in error for error in errors)


def test_out_of_range_limit_is_rejected() -> None:
    args, errors = validate_tool_call(
        "recall_similar_incidents",
        json.dumps({"service": "checkout-api", "symptom": "x", "limit": 500}),
    )
    assert args is None
    assert any("limit" in error for error in errors)


def test_wrong_type_is_rejected() -> None:
    args, errors = validate_tool_call(
        "recall_similar_incidents",
        json.dumps({"service": "checkout-api", "symptom": "x", "limit": "many"}),
    )
    assert args is None
    assert errors


def test_valid_call_parses_into_the_arg_model() -> None:
    args, errors = validate_tool_call(
        "recall_similar_incidents",
        json.dumps({"service": "checkout-api", "symptom": "p99 up", "incident_type": "latency"}),
    )
    assert errors == []
    assert isinstance(args, RecallSimilarIncidentsArgs)
    assert args.service == "checkout-api"
    assert args.limit == 5


def test_optional_fields_default_to_none() -> None:
    args, errors = validate_tool_call("get_service_map", "{}")
    assert errors == []
    assert isinstance(args, GetServiceMapArgs)
    assert args.service is None


def test_extra_fields_never_reach_the_handler() -> None:
    """Pydantic ignores extras by default; what matters is nothing unexpected is set."""
    args, errors = validate_tool_call(
        "get_service_map", json.dumps({"service": "checkout-api", "rm": "-rf /"})
    )
    if args is not None:
        assert not hasattr(args, "rm")


# --------------------------------------------------------------------------
# validate_grounding
# --------------------------------------------------------------------------
def _ctx(memory, catalog, mode: str = "on") -> ToolContext:
    return ToolContext(incident_id="INC-1", memory=memory, catalog=catalog, memory_mode=mode)


def _diagnosis_args(cited: list[str]) -> ProposeDiagnosisArgs:
    return ProposeDiagnosisArgs(
        hypothesis="Kafka consumer lag on payments-ledger",
        confidence="high",
        evidence_summary="Prior incident matched.",
        cited_memory_ids=cited,
    )


def test_citing_a_memory_that_was_never_recalled_is_rejected(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    errors = validate_grounding("propose_diagnosis", _diagnosis_args(["INC-9999:RESOLUTION"]), ctx)
    assert len(errors) == 1
    assert "not returned by any tool" in errors[0]


def test_citing_a_recalled_memory_is_accepted(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    ctx.record_hits(
        [
            RecallHit(
                memory_id="INC-2201:RESOLUTION",
                text="lag",
                metadata={"incident_id": "INC-2201", "event_type": "RESOLUTION"},
            )
        ]
    )
    args = _diagnosis_args(["INC-2201:RESOLUTION"])
    assert validate_grounding("propose_diagnosis", args, ctx) == []


def test_citing_memory_when_memory_is_off_is_rejected(memory, catalog) -> None:
    ctx = _ctx(memory, catalog, mode="off")
    errors = validate_grounding("propose_diagnosis", _diagnosis_args(["anything"]), ctx)
    assert any("memory_mode is off" in error for error in errors)


def test_resolution_before_diagnosis_is_rejected(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    args = ProposeResolutionArgs(fix="scale the consumer group", evidence_summary="none")
    errors = validate_grounding("propose_resolution", args, ctx)
    assert any("requires a diagnosis first" in error for error in errors)


def test_invented_runbook_id_is_rejected(memory, catalog) -> None:
    from src.contracts import Proposal

    ctx = _ctx(memory, catalog)
    ctx.diagnosis = Proposal(
        kind="diagnosis", status="proposed", content="lag", evidence_summary="e"
    )
    args = ProposeResolutionArgs(
        fix="do the thing", evidence_summary="e", runbook_id="RB-9999"
    )
    errors = validate_grounding("propose_resolution", args, ctx)
    assert any("RB-9999" in error for error in errors)


def test_real_runbook_id_is_accepted(memory, catalog) -> None:
    from src.contracts import Proposal

    ctx = _ctx(memory, catalog)
    ctx.diagnosis = Proposal(
        kind="diagnosis", status="proposed", content="lag", evidence_summary="e"
    )
    args = ProposeResolutionArgs(
        fix="scale the consumer group", evidence_summary="e", runbook_id="RB-014"
    )
    assert validate_grounding("propose_resolution", args, ctx) == []


def test_grounding_does_not_restrict_read_tools(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    args = RecallSimilarIncidentsArgs(service="checkout-api", symptom="latency")
    assert validate_grounding("recall_similar_incidents", args, ctx) == []


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def test_recall_reports_no_match_honestly(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    outcome = execute_tool_call(
        "recall_similar_incidents",
        RecallSimilarIncidentsArgs(service="checkout-api", symptom="unprecedented thing"),
        ctx,
    )
    assert "No relevant historical incident found" in outcome.summary
    assert outcome.data.get("no_match") is True
    assert ctx.recalled_ids == set()
    assert ctx.memory_trace_ids


def test_recall_searches_history_and_outcomes_separately(memory, catalog) -> None:
    """One broad recall ranks near-duplicate incident-opens above the outcomes."""
    ctx = _ctx(memory, catalog)
    execute_tool_call(
        "recall_similar_incidents",
        RecallSimilarIncidentsArgs(service="checkout-api", symptom="p99 latency after a deploy"),
        ctx,
    )
    scopes = [call["tags"] for call in memory.recall_kwargs]
    assert ["service:checkout-api", "event_type:incident_open"] in scopes
    assert ["service:checkout-api", "event_type:resolution"] in scopes


def test_recall_narrows_history_by_incident_type_but_not_outcomes(memory, catalog) -> None:
    """Outcomes stay service-wide so a misclassified incident type does not hide precedent."""
    ctx = _ctx(memory, catalog)
    execute_tool_call(
        "recall_similar_incidents",
        RecallSimilarIncidentsArgs(
            service="checkout-api", symptom="p99 latency", incident_type="latency"
        ),
        ctx,
    )
    scopes = [call["tags"] for call in memory.recall_kwargs]
    assert [
        "service:checkout-api",
        "event_type:incident_open",
        "incident_type:latency",
    ] in scopes
    assert ["service:checkout-api", "event_type:resolution"] in scopes


def test_outcome_queries_lead_with_the_service_name(memory, catalog) -> None:
    """Outcome memories are phrased around the service name, so the query must be too."""
    ctx = _ctx(memory, catalog)
    execute_tool_call(
        "recall_similar_incidents",
        RecallSimilarIncidentsArgs(service="checkout-api", symptom="p99 latency spike"),
        ctx,
    )
    outcome_query = next(
        call["query"] for call in memory.recall_kwargs if "event_type:resolution" in call["tags"]
    )
    assert outcome_query.startswith("checkout-api ")


def test_long_symptoms_are_compacted_for_outcome_queries(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    execute_tool_call(
        "recall_similar_incidents",
        RecallSimilarIncidentsArgs(service="checkout-api", symptom="word " * 60),
        ctx,
    )
    history_query, outcome_query = (call["query"] for call in memory.recall_kwargs)
    # History keeps the full narrative; the outcome query must stay short.
    assert len(str(history_query).split()) == 60
    assert len(str(outcome_query).split()) <= 16


def test_recall_returns_hits_and_records_their_ids(memory_with_history, catalog) -> None:
    ctx = _ctx(memory_with_history, catalog)
    outcome = execute_tool_call(
        "recall_similar_incidents",
        RecallSimilarIncidentsArgs(
            service="checkout-api", symptom="latency after payments-ledger deploy"
        ),
        ctx,
    )
    assert outcome.data["hits"]
    assert ctx.recalled_ids
    # Every id in the summary must also be in the recorded set, so the model can
    # only ever cite something real.
    for memory_id in ctx.recalled_ids:
        assert memory_id in outcome.summary


def test_recall_degrades_honestly_when_the_store_fails(catalog) -> None:
    from src.memory.fake import InMemoryMemoryStore

    ctx = _ctx(InMemoryMemoryStore(fail_recall=True), catalog)
    outcome = execute_tool_call(
        "recall_similar_incidents",
        RecallSimilarIncidentsArgs(service="checkout-api", symptom="latency"),
        ctx,
    )
    assert "Memory service unavailable" in outcome.summary
    assert outcome.data.get("degraded") is True
    assert ctx.recalled_ids == set()


def test_memory_off_makes_no_memory_calls(catalog) -> None:
    store = DisabledMemoryStore(bank_id="dejaops-test")
    ctx = _ctx(store, catalog, mode="off")
    for name, args in (
        ("recall_similar_incidents", RecallSimilarIncidentsArgs(service="a", symptom="b")),
        ("lookup_runbook", LookupRunbookArgs(suspected_cause="c")),
    ):
        outcome = execute_tool_call(name, args, ctx)
        assert "memory_mode=off" in outcome.summary
    assert ctx.memory_trace_ids == []
    assert ctx.recalled_ids == set()


def test_lookup_runbook_finds_a_validated_runbook(memory_with_history, catalog) -> None:
    ctx = _ctx(memory_with_history, catalog)
    outcome = execute_tool_call(
        "lookup_runbook",
        LookupRunbookArgs(
            suspected_cause="Kafka consumer lag on the payments topic", service="checkout-api"
        ),
        ctx,
    )
    assert "RB-014" in outcome.summary
    assert ctx.recalled_ids


def test_lookup_runbook_reports_no_match_when_empty(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    outcome = execute_tool_call(
        "lookup_runbook", LookupRunbookArgs(suspected_cause="nothing like this"), ctx
    )
    assert "No validated runbook" in outcome.summary


def test_lookup_runbook_does_not_threshold_the_exact_runbook_scope(memory, catalog) -> None:
    """Every runbook validated for a service is relevant by construction.

    Measured on the seeded bank: RB-014 is the only runbook validated for
    checkout-api, and it scores 0.0 against the query "checkout-api latency", so
    the default relevance threshold hid it completely.
    """
    ctx = _ctx(memory, catalog)
    execute_tool_call(
        "lookup_runbook",
        LookupRunbookArgs(suspected_cause="ledger confirmation timeouts", service="checkout-api"),
        ctx,
    )
    runbook_scope = next(
        call for call in memory.recall_kwargs if "event_type:runbook_entry" in call["tags"]
    )
    assert runbook_scope["min_score"] == 0.0
    assert runbook_scope["threshold"] == 0.0


def test_lookup_runbook_keeps_the_threshold_when_no_service_is_given(memory, catalog) -> None:
    """Without a service the scope is every runbook, so relevance must be scored."""
    ctx = _ctx(memory, catalog)
    execute_tool_call("lookup_runbook", LookupRunbookArgs(suspected_cause="lag"), ctx)
    runbook_scope = next(
        call for call in memory.recall_kwargs if "event_type:runbook_entry" in call["tags"]
    )
    assert runbook_scope["min_score"] is None
    assert runbook_scope["threshold"] == memory.min_score
    assert runbook_scope["tags"] == ["event_type:runbook_entry"]


def test_lookup_runbook_flags_unscoped_runbooks_as_possibly_inapplicable(memory, catalog) -> None:
    """A runbook for another service must not be presented as if it were ours."""
    memory.retain(
        MemoryEvent(
            event_type=EventType.RUNBOOK_ENTRY,
            incident_id="RUNBOOK-RB-021",
            content=(
                "Validated runbook RB-021 for root cause RC-002 to address Redis instability: "
                "pin the Redis primary and stop sentinel flapping."
            ),
            context="validated runbook",
            service="redis-cache",
            incident_type=None,
            runbook_id="RB-021",
            outcome=Outcome.CONFIRMED,
        )
    )
    ctx = _ctx(memory, catalog)
    outcome = execute_tool_call(
        "lookup_runbook",
        LookupRunbookArgs(suspected_cause="Redis instability and sentinel flapping"),
        ctx,
    )
    assert "no service filter" in outcome.summary
    assert "may not apply here" in outcome.summary
    assert any(hit["runbook_id"] == "RB-021" for hit in outcome.data["runbooks"])


def test_lookup_runbook_scoped_to_a_service_does_not_warn(memory_with_history, catalog) -> None:
    ctx = _ctx(memory_with_history, catalog)
    outcome = execute_tool_call(
        "lookup_runbook",
        LookupRunbookArgs(
            suspected_cause="Kafka consumer lag on the payments topic", service="checkout-api"
        ),
        ctx,
    )
    assert "may not apply here" not in outcome.summary


def test_lookup_runbook_reports_an_outage_differently_from_an_absence(memory, catalog) -> None:
    """A Hindsight outage must not be reported as "no validated runbook exists"."""
    from src.memory.fake import InMemoryMemoryStore

    ctx = _ctx(InMemoryMemoryStore(fail_recall=True), catalog)
    outcome = execute_tool_call(
        "lookup_runbook", LookupRunbookArgs(suspected_cause="anything", service="checkout-api"),
        ctx,
    )
    assert "Memory service unavailable" in outcome.summary
    assert outcome.data.get("degraded") is True
    assert "no_match" not in outcome.data


def test_lookup_runbook_never_scopes_runbooks_by_incident_type(memory, catalog) -> None:
    """Runbook entries carry no incident_type tag, so scoping by one matches nothing."""
    ctx = _ctx(memory, catalog)
    execute_tool_call(
        "lookup_runbook",
        LookupRunbookArgs(
            suspected_cause="lag", service="checkout-api", incident_type="latency"
        ),
        ctx,
    )
    runbook_scope = next(
        call for call in memory.recall_kwargs if "event_type:runbook_entry" in call["tags"]
    )
    assert all("incident_type" not in tag for tag in runbook_scope["tags"])


def test_lookup_runbook_leads_with_runbooks_then_validated_fixes(
    memory_with_history, catalog
) -> None:
    """The validated runbook is the most valuable memory, so it must not be truncated away."""
    ctx = _ctx(memory_with_history, catalog)
    execute_tool_call(
        "lookup_runbook",
        LookupRunbookArgs(
            suspected_cause="Kafka consumer lag on the payments topic",
            service="checkout-api",
            limit=1,
        ),
        ctx,
    )
    assert "event_type:runbook_entry" in ctx.memory.recall_kwargs[0]["tags"]


def test_get_service_map_resolves_dependencies(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    outcome = execute_tool_call(
        "get_service_map", GetServiceMapArgs(service="checkout-api"), ctx
    )
    assert "payments-ledger" in outcome.summary
    assert outcome.data["found"] is True
    assert {item["name"] for item in outcome.data["dependencies"]} == {
        "payments-ledger",
        "redis-cache",
    }


def test_get_service_map_handles_an_unknown_service(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    outcome = execute_tool_call("get_service_map", GetServiceMapArgs(service="nope"), ctx)
    assert "not in the catalog" in outcome.summary
    assert outcome.data["found"] is False


def test_get_service_map_lists_the_estate(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    outcome = execute_tool_call("get_service_map", GetServiceMapArgs(), ctx)
    assert "checkout-api" in outcome.summary
    assert len(outcome.data["services"]) == len(catalog.services)


# --------------------------------------------------------------------------
# Proposal tools: the critical safety property
# --------------------------------------------------------------------------
def test_proposal_tools_never_write_to_memory(memory_with_history, catalog) -> None:
    """The whole point of the confirmation boundary. Retain count must not move."""
    ctx = _ctx(memory_with_history, catalog)
    before = memory_with_history.retain_calls

    execute_tool_call(
        "propose_diagnosis",
        ProposeDiagnosisArgs(
            hypothesis="Kafka consumer lag on payments-ledger",
            confidence="high",
            evidence_summary="Prior incident matched.",
        ),
        ctx,
    )
    execute_tool_call(
        "propose_resolution",
        ProposeResolutionArgs(fix="Scale the consumer group", evidence_summary="Matches RB-014."),
        ctx,
    )

    assert memory_with_history.retain_calls == before
    assert len(memory_with_history.events) == 1


def test_propose_diagnosis_is_marked_proposed(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    outcome = execute_tool_call(
        "propose_diagnosis",
        ProposeDiagnosisArgs(hypothesis="lag", confidence="medium", evidence_summary="lag seen"),
        ctx,
    )
    assert ctx.diagnosis is not None
    assert ctx.diagnosis.status == "proposed"
    assert ctx.diagnosis.kind == "diagnosis"
    assert "awaiting operator confirmation" in outcome.summary
    assert "not confirmed" in outcome.summary


def test_propose_resolution_is_marked_pending_confirmation(memory, catalog) -> None:
    from src.contracts import Proposal

    ctx = _ctx(memory, catalog)
    ctx.diagnosis = Proposal(
        kind="diagnosis", status="proposed", content="lag", evidence_summary="e"
    )
    outcome = execute_tool_call(
        "propose_resolution",
        ProposeResolutionArgs(
            fix="scale consumers", evidence_summary="matches RB-014", runbook_id="RB-014"
        ),
        ctx,
    )
    assert ctx.resolution is not None
    assert ctx.resolution.status == "pending_confirmation"
    assert ctx.resolution.runbook_id == "RB-014"
    assert "NOT resolved" in outcome.summary


def test_proposing_a_diagnosis_twice_updates_rather_than_duplicates(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    for hypothesis in ("first guess", "refined guess"):
        execute_tool_call(
            "propose_diagnosis",
            ProposeDiagnosisArgs(
                hypothesis=hypothesis, confidence="low", evidence_summary="evidence"
            ),
            ctx,
        )
    assert ctx.diagnosis is not None
    assert ctx.diagnosis.content == "refined guess"


def test_executing_an_unregistered_tool_raises(memory, catalog) -> None:
    ctx = _ctx(memory, catalog)
    with pytest.raises(KeyError):
        execute_tool_call("not_a_tool", GetServiceMapArgs(), ctx)


def test_seeded_history_is_scoped_by_service(memory_with_history, catalog) -> None:
    """A tag mismatch must not leak another service's history."""
    ctx = _ctx(memory_with_history, catalog)
    outcome = execute_tool_call(
        "recall_similar_incidents",
        RecallSimilarIncidentsArgs(
            service="kafka-bus", symptom="latency after payments-ledger deploy"
        ),
        ctx,
    )
    assert outcome.data.get("no_match") is True


def test_seeded_event_uses_expected_tags(memory_with_history) -> None:
    seed_lag_incident(memory_with_history)
    event: MemoryEvent = memory_with_history.events[0]
    assert event.event_type is EventType.RESOLUTION
    assert event.outcome is Outcome.CONFIRMED
    assert "service:checkout-api" in event.tags()
    assert "event_type:resolution" in event.tags()
