"""Learning-evaluation tests.

The scoring functions are pure, so the metric definitions are testable without
Groq, without Hindsight, and without a bank. What is *not* tested here is whether
the agent diagnoses well - that is a measurement, not an assertion.
"""

from __future__ import annotations

from scripts.evaluate_learning import (
    alert_from_incident,
    compute_metrics,
    eval_pairs,
    is_grounded,
    load_seed,
    seed_subset,
)
from src.memory.schema import EventType, MemoryEvent


def _incident(
    incident_id: str,
    *,
    pattern: str | None = "lag",
    occurrence: int | None = 1,
    service: str = "checkout-api",
    runbook: str | None = None,
) -> dict:
    return {
        "incident_id": incident_id,
        "service": service,
        "severity": "p1",
        "incident_type": "latency",
        "environment": "prod",
        "pattern_key": pattern,
        "pattern_occurrence": occurrence,
        "alert": {
            "title": f"{service} is slow",
            "summary": "p99 above budget",
            "source": "pagerduty-sim",
            "fired_at": "2026-08-01T00:00:00Z",
            "signals": {"error_samples": ["a", "b"]},
        },
        "resolution": {"validated_runbook_id": runbook},
    }


def _event(
    incident_id: str, *, runbook_id: str | None = None, event_type: EventType | None = None
) -> MemoryEvent:
    if event_type is None:
        if incident_id.startswith("RUNBOOK-"):
            event_type = EventType.RUNBOOK_ENTRY
        else:
            event_type = EventType.RESOLUTION if runbook_id else EventType.INCIDENT_OPEN
    return MemoryEvent(
        event_type=event_type,
        incident_id=incident_id,
        content=f"memory for {incident_id}",
        service="checkout-api",
        runbook_id=runbook_id,
    )


# --------------------------------------------------------------------------
# seed_subset: the leak guard
# --------------------------------------------------------------------------
def test_seed_subset_never_contains_the_incident_being_evaluated() -> None:
    """The whole learning curve rests on this.

    If the evaluated occurrence were seeded, the agent could recall the answer
    it is being asked for and every score above cutoff 2 would be meaningless.
    """
    incidents = [
        _incident("INC-A1", occurrence=1),
        _incident("INC-A2", occurrence=2),
        _incident("INC-A3", occurrence=3, runbook="RB-014"),
        _incident("INC-A4", occurrence=4, runbook="RB-014"),
    ]
    events = [
        _event(i["incident_id"], runbook_id=i["resolution"]["validated_runbook_id"])
        for i in incidents
    ]

    for cutoff, evaluated in eval_pairs(incidents, min_occurrence=3):
        seeded = {event.incident_id for event in seed_subset(events, incidents, cutoff)}
        assert evaluated not in seeded, f"cutoff {cutoff} leaked {evaluated}"


def test_seed_subset_keeps_strictly_earlier_occurrences() -> None:
    incidents = [
        _incident("INC-A1", occurrence=1),
        _incident("INC-A2", occurrence=2),
        _incident("INC-A3", occurrence=3, runbook="RB-014"),
    ]
    events = [_event(i["incident_id"]) for i in incidents]
    seeded = {event.incident_id for event in seed_subset(events, incidents, cutoff=2)}
    assert seeded == {"INC-A1", "INC-A2"}


def test_seed_subset_always_keeps_noise_incidents() -> None:
    """A noise incident never resolved to a cause, but it is still real history."""
    incidents = [
        _incident("INC-A1", occurrence=1),
        _incident("INC-N1", pattern=None, occurrence=None),
    ]
    events = [_event("INC-A1"), _event("INC-N1")]
    seeded = {event.incident_id for event in seed_subset(events, incidents, cutoff=0)}
    assert "INC-N1" in seeded


def test_seed_subset_holds_back_runbooks_not_yet_validated() -> None:
    """A runbook only exists once an incident at or before the cutoff validated it."""
    incidents = [
        _incident("INC-A1", occurrence=1),
        _incident("INC-A3", occurrence=3, runbook="RB-014"),
    ]
    events = [
        _event("INC-A1"),
        _event("INC-A3"),
        _event("RUNBOOK-RB-014", runbook_id="RB-014"),
    ]
    before = seed_subset(events, incidents, cutoff=1)
    after = seed_subset(events, incidents, cutoff=3)
    assert not [e for e in before if e.runbook_id]
    assert [e for e in after if e.runbook_id]


def test_seed_subset_scales_with_the_cutoff() -> None:
    incidents = [
        _incident("INC-A1", occurrence=1),
        _incident("INC-A2", occurrence=2),
        _incident("INC-A3", occurrence=3, runbook="RB-014"),
        _incident("INC-A4", occurrence=4, runbook="RB-014"),
    ]
    events = [_event(i["incident_id"]) for i in incidents]
    sizes = [len(seed_subset(events, incidents, c)) for c in range(4)]
    assert sizes == sorted(sizes)
    assert sizes[0] == 0


# --------------------------------------------------------------------------
# eval_pairs
# --------------------------------------------------------------------------
def test_eval_pairs_pair_each_occurrence_with_the_history_before_it() -> None:
    incidents = [_incident(f"INC-A{n}", occurrence=n, runbook="RB-014") for n in range(1, 5)]
    assert eval_pairs(incidents, min_occurrence=3) == [(2, "INC-A3"), (3, "INC-A4")]


def test_eval_pairs_skip_occurrences_with_no_validated_fix_in_the_label() -> None:
    """Below the minimum there is no fix to hit, so the question has no answer."""
    incidents = [_incident(f"INC-A{n}", occurrence=n, runbook=None) for n in range(1, 3)]
    assert eval_pairs(incidents, min_occurrence=3) == []


def test_eval_pairs_keep_patterns_separate() -> None:
    incidents = [
        _incident("INC-A3", pattern="a", occurrence=3, runbook="RB-014"),
        _incident("INC-B3", pattern="b", occurrence=3, runbook="RB-032"),
    ]
    assert eval_pairs(incidents, min_occurrence=3) == [(2, "INC-A3"), (2, "INC-B3")]


def test_eval_pairs_are_stable_across_calls() -> None:
    incidents = [_incident(f"INC-A{n}", occurrence=n, runbook="RB-014") for n in range(1, 6)]
    assert eval_pairs(incidents) == eval_pairs(incidents)


# --------------------------------------------------------------------------
# compute_metrics
# --------------------------------------------------------------------------
def _record(
    status: str = "completed",
    *,
    cause: bool = True,
    fix: bool = True,
    grounded: bool = True,
    cutoff: int = 2,
) -> dict:
    return {
        "status": status,
        "cause_hit": cause,
        "fix_hit": fix,
        "grounded": grounded,
        "cutoff": cutoff,
        "steps": 4,
        "used_fallback": False,
    }


def test_metrics_are_rates_over_completed_runs() -> None:
    records = [_record(cause=True), _record(cause=False), _record(cause=True), _record(cause=False)]
    assert compute_metrics(records)["root_cause_hit_at_1"] == 0.5


def test_incomplete_runs_are_excluded_from_the_denominator() -> None:
    """A rate-limited run is not a wrong diagnosis, and counting it as one
    would understate the agent and would not reproduce."""
    records = [_record(cause=True), _record(cause=False), _record("model_failed", cause=False)]
    metrics = compute_metrics(records)
    assert metrics["root_cause_hit_at_1"] == 0.5
    assert metrics["n_scored"] == 2
    assert metrics["n_incomplete"] == 1
    assert metrics["incomplete_reasons"] == ["model_failed"]


def test_an_all_incomplete_run_reports_none_rather_than_zero() -> None:
    metrics = compute_metrics([_record("model_failed")])
    assert metrics["root_cause_hit_at_1"] is None
    assert metrics["grounded_response_rate"] is None


def test_metrics_on_no_records_do_not_divide_by_zero() -> None:
    assert compute_metrics([])["root_cause_hit_at_1"] is None


# --------------------------------------------------------------------------
# is_grounded: provenance, not the model's word
# --------------------------------------------------------------------------
LABELS = {
    "INC-1": {"root_cause_id": "RC-001", "validated_runbook_id": "RB-014"},
    "INC-2": {"root_cause_id": "RC-001", "validated_runbook_id": "RB-014"},
    "INC-3": {"root_cause_id": "RC-002", "validated_runbook_id": "RB-021"},
}


def test_no_citations_is_not_grounded() -> None:
    assert not is_grounded([], {}, LABELS["INC-1"], LABELS, "INC-1")


def test_a_citation_the_run_was_never_shown_is_not_grounded() -> None:
    assert not is_grounded(["mem-unknown"], {}, LABELS["INC-1"], LABELS, "INC-1")


def test_citing_the_labeled_runbook_is_grounded() -> None:
    provenance = {"mem-1": {"memory_id": "mem-1", "runbook_id": "RB-014", "incident_id": "INC-2"}}
    assert is_grounded(["mem-1"], provenance, LABELS["INC-1"], LABELS, "INC-1")


def test_citing_a_prior_incident_with_the_same_root_cause_is_grounded() -> None:
    provenance = {"mem-2": {"memory_id": "mem-2", "runbook_id": None, "incident_id": "INC-2"}}
    assert is_grounded(["mem-2"], provenance, LABELS["INC-1"], LABELS, "INC-1")


def test_citing_a_prior_incident_with_a_different_root_cause_is_not_grounded() -> None:
    provenance = {"mem-3": {"memory_id": "mem-3", "runbook_id": None, "incident_id": "INC-3"}}
    assert not is_grounded(["mem-3"], provenance, LABELS["INC-1"], LABELS, "INC-1")


def test_citing_the_incidents_own_memory_is_not_grounded() -> None:
    """Its own alert is not evidence for its own diagnosis."""
    provenance = {"mem-4": {"memory_id": "mem-4", "runbook_id": None, "incident_id": "INC-1"}}
    assert not is_grounded(["mem-4"], provenance, LABELS["INC-1"], LABELS, "INC-1")


def test_one_supporting_citation_among_unsupporting_ones_is_enough() -> None:
    provenance = {
        "mem-3": {"memory_id": "mem-3", "runbook_id": None, "incident_id": "INC-3"},
        "mem-2": {"memory_id": "mem-2", "runbook_id": None, "incident_id": "INC-2"},
    }
    assert is_grounded(["mem-3", "mem-2"], provenance, LABELS["INC-1"], LABELS, "INC-1")


# --------------------------------------------------------------------------
# Against the real seed data
# --------------------------------------------------------------------------
def test_alert_from_incident_flattens_the_seed_shape() -> None:
    incidents, _ = load_seed()
    alert = alert_from_incident(incidents[0])
    assert alert.service == incidents[0]["service"]
    assert alert.title == incidents[0]["alert"]["title"]
    assert alert.error_samples == incidents[0]["alert"]["signals"]["error_samples"]


def test_real_eval_pairs_never_leak_and_always_have_a_fix_to_hit() -> None:
    incidents, labels = load_seed()
    pairs = eval_pairs(incidents)
    assert pairs, "the seeded dataset must produce an evaluation set"
    for cutoff, incident_id in pairs:
        label = labels[incident_id]
        assert label["validated_runbook_id"], f"{incident_id} has no fix to hit"
        occurrence = next(
            i["pattern_occurrence"] for i in incidents if i["incident_id"] == incident_id
        )
        assert cutoff == occurrence - 1


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
def _cache_record(status: str, incident_id: str = "INC-A3", cutoff: int = 2) -> dict:
    return {
        "mode": "curve",
        "cutoff": cutoff,
        "memory_mode": "on",
        "incident_id": incident_id,
        "status": status,
    }


def test_incomplete_runs_are_not_cache_hits(tmp_path) -> None:
    """A rate-limited run must be retried, not frozen into the curve as absent."""
    from scripts.evaluate_learning import append_cache, load_cache

    path = tmp_path / "eval.jsonl"
    append_cache(path, _cache_record("model_failed"))
    assert load_cache(path) == {}


def test_completed_runs_are_cache_hits(tmp_path) -> None:
    from scripts.evaluate_learning import append_cache, load_cache

    path = tmp_path / "eval.jsonl"
    append_cache(path, _cache_record("completed"))
    assert len(load_cache(path)) == 1


def test_the_latest_completed_record_wins(tmp_path) -> None:
    from scripts.evaluate_learning import append_cache, load_cache

    path = tmp_path / "eval.jsonl"
    append_cache(path, _cache_record("model_failed"))
    append_cache(path, {**_cache_record("completed"), "cause_hit": True})
    cached = load_cache(path)
    assert len(cached) == 1
    assert next(iter(cached.values()))["cause_hit"] is True


def test_a_missing_cache_file_is_not_an_error(tmp_path) -> None:
    from scripts.evaluate_learning import load_cache

    assert load_cache(tmp_path / "nope.jsonl") == {}
