"""The teach-then-replay invariant.

The demo scenario only demonstrates learning if two things hold at once:

1. the ground truth is **in the catalog**, or the agent cannot name it even after
   being taught, and the grounding guards would reject it if it guessed; and
2. the ground truth is **not in memory**, or the agent could answer before the
   operator teaches anything and the before/after comparison would be a lie.

Those pull in opposite directions, which is why they are tested together.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_learning import load_seed, seed_subset
from scripts.seed_memory import build_seed_events
from src.catalog import load_catalog

DEMO_PATH = Path("data/demo/novel_incident.json")


@pytest.fixture(scope="module")
def demo() -> dict:
    return json.loads(DEMO_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def events():
    return build_seed_events()


# --------------------------------------------------------------------------
# In the catalog
# --------------------------------------------------------------------------
def test_the_demo_cause_is_in_the_catalog(demo) -> None:
    """Without this the agent cannot name the cause, taught or not."""
    catalog = load_catalog()
    truth = demo["hidden_ground_truth"]
    assert catalog.root_cause(truth["root_cause_id"]) is not None


def test_the_demo_runbook_is_in_the_catalog(demo) -> None:
    catalog = load_catalog()
    runbook = catalog.runbook(demo["hidden_ground_truth"]["validated_runbook_id"])
    assert runbook is not None
    assert runbook.root_cause_id == demo["hidden_ground_truth"]["root_cause_id"]


def test_the_demo_incident_agrees_with_its_hidden_ground_truth(demo) -> None:
    truth = demo["hidden_ground_truth"]
    assert demo["diagnosis"]["actual_root_cause_id"] == truth["root_cause_id"]
    assert demo["resolution"]["validated_runbook_id"] == truth["validated_runbook_id"]
    assert demo["resolution"]["fix"] == truth["validated_fix"]


# --------------------------------------------------------------------------
# Not in memory
# --------------------------------------------------------------------------
def test_no_seeded_incident_validates_the_demo_runbook(demo) -> None:
    """A runbook is only promoted to memory by an incident that validated it."""
    incidents, _ = load_seed()
    runbook_id = demo["hidden_ground_truth"]["validated_runbook_id"]
    validators = [
        incident["incident_id"]
        for incident in incidents
        if (incident.get("resolution") or {}).get("validated_runbook_id") == runbook_id
    ]
    assert validators == []


def test_the_demo_runbook_is_never_promoted_to_memory(demo, events) -> None:
    runbook_id = demo["hidden_ground_truth"]["validated_runbook_id"]
    assert [event for event in events if event.runbook_id == runbook_id] == []


def test_no_seeded_memory_mentions_the_demo_cause(demo, events) -> None:
    """The cause id appears in no memory text, so recall cannot surface it."""
    root_cause_id = demo["hidden_ground_truth"]["root_cause_id"]
    assert [event for event in events if root_cause_id in event.content] == []


def test_the_demo_ground_truth_is_absent_at_every_history_cutoff(demo, events) -> None:
    """Holds even with the entire seeded history in the bank, not just early on."""
    incidents, _ = load_seed()
    runbook_id = demo["hidden_ground_truth"]["validated_runbook_id"]
    root_cause_id = demo["hidden_ground_truth"]["root_cause_id"]

    for cutoff in (2, 3, 6, 99):
        subset = seed_subset(events, incidents, cutoff)
        assert [e for e in subset if e.runbook_id == runbook_id] == []
        assert [e for e in subset if root_cause_id in e.content] == []


def test_the_demo_has_no_symptom_signature_in_history(demo) -> None:
    """Novel in shape, not just in id: no seeded incident shares service and type."""
    incidents, _ = load_seed()
    collisions = [
        incident["incident_id"]
        for incident in incidents
        if incident["service"] == demo["service"]
        and incident["incident_type"] == demo["incident_type"]
    ]
    assert collisions == [], f"the demo shape is not novel: {collisions}"


def test_the_nearest_history_is_a_different_cause(demo) -> None:
    """The service does have history, and it points somewhere else.

    This is what makes the demo honest rather than a system that simply has
    nothing to say: recall will return real webhook-dispatcher incidents whose
    cause is a retry storm, and the operator corrects it to a key rotation.
    """
    incidents, labels = load_seed()
    webhook = [i for i in incidents if i["service"] == demo["service"]]
    assert webhook, "the demo service must have some history to be tempted by"
    assert all(
        labels[i["incident_id"]]["root_cause_id"] != demo["hidden_ground_truth"]["root_cause_id"]
        for i in webhook
    )


def test_the_demo_is_not_in_the_seeded_incident_set(demo) -> None:
    incidents, _ = load_seed()
    assert demo["incident_id"] not in {i["incident_id"] for i in incidents}
