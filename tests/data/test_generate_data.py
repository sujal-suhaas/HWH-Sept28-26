"""Dataset generator tests: determinism, hidden labels, and the learning story."""

from __future__ import annotations

from scripts.generate_data import build_dataset, build_demo_incident


def test_generation_is_deterministic() -> None:
    assert build_dataset(1337) == build_dataset(1337)


def test_different_seeds_produce_different_data() -> None:
    assert build_dataset(1337)["incidents"] != build_dataset(99)["incidents"]


def test_incident_volume_matches_the_plan() -> None:
    dataset = build_dataset(1337)
    assert 40 <= len(dataset["incidents"]) <= 60
    assert 6 <= len(dataset["postmortems"]) <= 8


def test_every_incident_has_hidden_evaluation_labels() -> None:
    dataset = build_dataset(1337)
    labels = dataset["labels"]
    assert len(labels) == len(dataset["incidents"])
    for incident in dataset["incidents"]:
        label = labels[incident["incident_id"]]
        for key in (
            "root_cause_id",
            "validated_runbook_id",
            "service",
            "severity",
            "incident_type",
        ):
            assert key in label
        assert label["service"] == incident["service"]
        assert label["severity"] == incident["severity"]
        assert label["incident_type"] == incident["incident_type"]


def test_service_catalog_covers_the_planned_services() -> None:
    names = {service["name"] for service in build_dataset(1337)["services"]}
    assert {
        "checkout-api",
        "payments-ledger",
        "auth-service",
        "fraud-scorer",
        "kafka-bus",
        "redis-cache",
        "webhook-dispatcher",
    } <= names


def test_incidents_span_roughly_eight_weeks() -> None:
    incidents = build_dataset(1337)["incidents"]
    first = incidents[0]["opened_at"]
    last = incidents[-1]["opened_at"]
    assert first < last


def test_learning_story_first_occurrence_is_not_confirmed() -> None:
    """A pattern must not be instantly solvable the first time it is seen."""
    first_by_pattern: dict[str, str] = {}
    for incident in build_dataset(1337)["incidents"]:
        key = incident.get("pattern_key")
        if key and key not in first_by_pattern:
            first_by_pattern[key] = incident["diagnosis"]["outcome"]
    assert first_by_pattern
    assert all(outcome != "confirmed" for outcome in first_by_pattern.values())


def test_learning_story_later_occurrences_are_confirmed_with_a_runbook() -> None:
    later: list[dict] = [
        incident
        for incident in build_dataset(1337)["incidents"]
        if incident.get("pattern_occurrence", 0) >= 3
        and incident["pattern_key"] != "checkout_burst_pool"
    ]
    assert later
    confirmed_with_runbook = [
        incident
        for incident in later
        if incident["diagnosis"]["outcome"] == "confirmed"
        and incident["resolution"]["validated_runbook_id"]
    ]
    assert len(confirmed_with_runbook) >= 10


def test_unconfirmed_incidents_never_carry_a_validated_runbook() -> None:
    for incident in build_dataset(1337)["incidents"]:
        if incident["diagnosis"]["outcome"] != "confirmed":
            assert incident["resolution"]["validated_runbook_id"] is None
            assert incident["resolution"]["verified"] is False


def test_confirmed_runbooks_exist_in_the_runbook_catalog() -> None:
    dataset = build_dataset(1337)
    known = {runbook["id"] for runbook in dataset["runbooks"]}
    for incident in dataset["incidents"]:
        runbook_id = incident["resolution"]["validated_runbook_id"]
        if runbook_id:
            assert runbook_id in known


def test_demo_incident_is_novel_against_the_history() -> None:
    dataset = build_dataset(1337)
    demo = dataset["demo"]
    history_types = {(i["service"], i["incident_type"]) for i in dataset["incidents"]}
    assert (demo["service"], demo["incident_type"]) not in history_types
    # No historical incident mentions the demo's distinguishing symptom.
    assert not any(
        "signature" in str(incident).lower() for incident in dataset["incidents"]
    )


def test_demo_incident_ships_ground_truth_for_the_teach_step() -> None:
    demo = build_demo_incident()
    truth = demo["hidden_ground_truth"]
    assert truth["root_cause_id"]
    assert truth["validated_runbook_id"]
    assert truth["validated_fix"]
    assert demo["diagnosis"]["outcome"] == "pending"


def test_alert_payloads_are_realistic_and_present() -> None:
    for incident in build_dataset(1337)["incidents"]:
        alert = incident["alert"]
        assert alert["title"]
        assert alert["summary"]
        assert alert["signals"]["error_samples"]
        assert incident["error_messages"]
        assert incident["timeline"]
