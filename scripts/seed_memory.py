#!/usr/bin/env python
"""Seed the Hindsight Memory Bank with the NimbusPay incident history.

Only qualifying, backend-authoritative events are retained. Each retained item
is derived from an incident whose outcome an operator already confirmed (or
explicitly marked inconclusive), so seeding history does not invent authority.

Idempotent: already-retained document ids are recorded in a local state file and
skipped on the next run. Use ``--reset`` to drop the bank and start clean.

    uv run python scripts/seed_memory.py --dry-run
    uv run python scripts/seed_memory.py
    uv run python scripts/seed_memory.py --reset
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.memory.schema import EventType, MemoryEvent, Outcome

SEED_DIR = Path("data/seed")
STATE_PATH = Path("data/store/seed_state.json")


# --------------------------------------------------------------------------
# Event construction (pure - testable without a network)
# --------------------------------------------------------------------------
def _incident_open_event(incident: dict[str, Any]) -> MemoryEvent:
    alert = incident["alert"]
    signals = "; ".join(alert.get("signals", {}).get("error_samples", []))
    content = (
        f"Incident {incident['incident_id']} opened on {incident['service']} "
        f"(severity {incident['severity']}, {incident['incident_type']}). "
        f"Alert: {alert['title']}. Summary: {alert['summary']}"
    )
    if signals:
        content += f" Initial signals: {signals}"
    return MemoryEvent(
        event_type=EventType.INCIDENT_OPEN,
        incident_id=incident["incident_id"],
        content=content,
        context="incident open",
        service=incident["service"],
        severity=incident["severity"],
        incident_type=incident["incident_type"],
        environment=incident.get("environment", "prod"),
        timestamp=datetime.fromisoformat(incident["opened_at"].replace("Z", "+00:00")),
    )


def _diagnosis_event(incident: dict[str, Any]) -> MemoryEvent | None:
    diagnosis = incident["diagnosis"]
    outcome = diagnosis.get("outcome")
    if outcome not in {"confirmed", "rejected", "inconclusive"}:
        return None
    mapping = {
        "confirmed": Outcome.CONFIRMED,
        "rejected": Outcome.REJECTED,
        "inconclusive": Outcome.INCONCLUSIVE,
    }
    actual = diagnosis.get("actual_root_cause_id")
    content = (
        f"Diagnosis for incident {incident['incident_id']} ({incident['service']}, "
        f"{incident['incident_type']}) was {outcome}. "
        f"Agent hypothesis: {diagnosis['hypothesis']}."
    )
    if outcome == "confirmed" and actual:
        content += f" Confirmed root cause: {actual}."
    elif outcome == "rejected":
        content += " The proposed hypothesis was rejected by the operator."
    return MemoryEvent(
        event_type=EventType.DIAGNOSIS,
        incident_id=incident["incident_id"],
        content=content,
        context="diagnosis outcome",
        service=incident["service"],
        severity=incident["severity"],
        incident_type=incident["incident_type"],
        environment=incident.get("environment", "prod"),
        outcome=mapping[outcome],
        timestamp=datetime.fromisoformat(incident["opened_at"].replace("Z", "+00:00")),
    )


def _resolution_event(incident: dict[str, Any]) -> MemoryEvent | None:
    resolution = incident["resolution"]
    if incident["diagnosis"].get("outcome") != "confirmed" or not resolution.get("verified"):
        return None
    runbook_id = resolution.get("validated_runbook_id")
    content = (
        f"Resolution for incident {incident['incident_id']} ({incident['service']}, "
        f"{incident['incident_type']}): validated fix - {resolution['fix']}. "
        f"Verified by the operator. "
        f"Time to resolve: {resolution['time_to_resolve_minutes']} minutes."
    )
    if runbook_id:
        content += f" Runbook: {runbook_id}."
    return MemoryEvent(
        event_type=EventType.RESOLUTION,
        incident_id=incident["incident_id"],
        content=content,
        context="validated resolution",
        service=incident["service"],
        severity=incident["severity"],
        incident_type=incident["incident_type"],
        environment=incident.get("environment", "prod"),
        outcome=Outcome.CONFIRMED,
        runbook_id=runbook_id,
        timestamp=datetime.fromisoformat(incident["opened_at"].replace("Z", "+00:00")),
    )


def _postmortem_event(
    postmortem: dict[str, Any], incidents: dict[str, dict[str, Any]]
) -> MemoryEvent:
    incident = incidents.get(postmortem["incident_id"], {})
    contributing = "; ".join(postmortem.get("contributing_factors", []))
    prevention = "; ".join(postmortem.get("prevention", []))
    content = (
        f"Postmortem {postmortem['postmortem_id']} for incident {postmortem['incident_id']} "
        f"({postmortem['service']}): root cause {postmortem['root_cause_id']} - "
        f"{postmortem['root_cause']}. {postmortem['detail']} "
        f"Contributing factors: {contributing}. Prevention: {prevention}."
    )
    return MemoryEvent(
        event_type=EventType.POSTMORTEM,
        incident_id=postmortem["incident_id"],
        content=content,
        context="postmortem",
        service=postmortem["service"],
        severity=incident.get("severity"),
        incident_type=incident.get("incident_type"),
        environment=incident.get("environment", "prod"),
        timestamp=datetime.fromisoformat(
            incident.get("opened_at", "2026-01-01T00:00:00Z").replace("Z", "+00:00")
        ),
    )


def _runbook_event(
    runbook: dict[str, Any], services: list[str], promoted_at: datetime
) -> MemoryEvent:
    steps = " ".join(f"{index + 1}) {step}" for index, step in enumerate(runbook["steps"]))
    content = (
        f"Validated runbook {runbook['id']} for root cause {runbook['root_cause_id']}: "
        f"{runbook['title']}. Steps: {steps}"
    )
    return MemoryEvent(
        event_type=EventType.RUNBOOK_ENTRY,
        incident_id=f"RUNBOOK-{runbook['id']}",
        content=content,
        context="validated runbook",
        service=services[0] if services else None,
        incident_type=None,
        runbook_id=runbook["id"],
        outcome=Outcome.CONFIRMED,
        timestamp=promoted_at,
    )


def build_seed_events(seed_dir: Path = SEED_DIR) -> list[MemoryEvent]:
    """Build every durable memory event from the seed files. Pure function."""
    incidents_payload = json.loads((seed_dir / "incidents.json").read_text(encoding="utf-8"))
    postmortems_payload = json.loads((seed_dir / "postmortems.json").read_text(encoding="utf-8"))
    runbooks_payload = json.loads((seed_dir / "runbooks.json").read_text(encoding="utf-8"))

    incidents: list[dict[str, Any]] = incidents_payload["incidents"]
    by_id = {incident["incident_id"]: incident for incident in incidents}

    events: list[MemoryEvent] = []
    for incident in incidents:
        events.append(_incident_open_event(incident))
        diagnosis = _diagnosis_event(incident)
        if diagnosis:
            events.append(diagnosis)
        resolution = _resolution_event(incident)
        if resolution:
            events.append(resolution)

    for postmortem in postmortems_payload["postmortems"]:
        events.append(_postmortem_event(postmortem, by_id))

    # Only runbooks that actually resolved an incident become runbook memory.
    confirmed_runbooks = {
        incident["resolution"]["validated_runbook_id"]
        for incident in incidents
        if incident["resolution"].get("validated_runbook_id")
    }
    service_by_runbook: dict[str, list[str]] = {}
    promoted_at_by_runbook: dict[str, datetime] = {}
    for incident in incidents:
        runbook_id = incident["resolution"].get("validated_runbook_id")
        if runbook_id:
            service_by_runbook.setdefault(runbook_id, []).append(incident["service"])
            opened = datetime.fromisoformat(incident["opened_at"].replace("Z", "+00:00"))
            promoted_at_by_runbook[runbook_id] = max(
                opened, promoted_at_by_runbook.get(runbook_id, opened)
            )

    for runbook in runbooks_payload["runbooks"]:
        if runbook["id"] in confirmed_runbooks:
            events.append(
                _runbook_event(
                    runbook,
                    service_by_runbook.get(runbook["id"], []),
                    promoted_at_by_runbook[runbook["id"]],
                )
            )

    return events


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _summarize(events: list[MemoryEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.event_type.value] = counts.get(event.event_type.value, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed the Hindsight memory bank with NimbusPay history."
    )
    parser.add_argument("--seed-dir", type=Path, default=SEED_DIR)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--bank", type=str, default=None, help="override HINDSIGHT_BANK_ID")
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would be retained, call nothing"
    )
    parser.add_argument("--reset", action="store_true", help="delete and recreate the bank first")
    parser.add_argument("--limit", type=int, default=None, help="retain at most N events")
    args = parser.parse_args()

    events = build_seed_events(args.seed_dir)
    if args.limit:
        events = events[: args.limit]

    counts = _summarize(events)
    print(f"built {len(events)} memory events from {args.seed_dir}:")
    for event_type, count in sorted(counts.items()):
        print(f"  {event_type:<20} {count}")

    if args.dry_run:
        print("\n--dry-run: nothing was sent to Hindsight")
        for event in events[:3]:
            print(f"\n[{event.event_type.value}] {event.incident_id}")
            print(f"  tags:     {event.tags()}")
            print(f"  metadata: {event.metadata()}")
            print(f"  content:  {event.content[:160]}...")
        return 0

    from src.config import get_settings
    from src.logging_setup import configure_logging
    from src.memory import build_memory_store

    settings = get_settings()
    if args.bank:
        settings = settings.model_copy(update={"hindsight_bank_id": args.bank})
    configure_logging(settings)

    store = build_memory_store(settings)
    if store.bank_id != settings.hindsight_bank_id:
        print("memory is disabled; set MEMORY_MODE=on to seed", file=sys.stderr)
        return 2

    try:
        if args.reset:
            print(f"resetting bank {store.bank_id} ...")
            reset_trace = _reset_bank(store)
            if not reset_trace.success:
                print(f"reset failed: {reset_trace.error_message}", file=sys.stderr)
                return 1

        bank_trace = store.create_bank_if_needed()
        if not bank_trace.success:
            print(f"could not create bank: {bank_trace.error_message}", file=sys.stderr)
            return 1

        state = {} if args.reset else _load_state(args.state)
        if state.get("bank_id") == store.bank_id:
            done: set[str] = set(state.get("document_ids", []))
        else:
            done = set()

        retained = 0
        skipped = 0
        failed = 0
        for event in events:
            document_id = event.document_id_or_default()
            if document_id in done:
                skipped += 1
                continue
            trace = store.retain(event)
            if trace.success:
                done.add(document_id)
                retained += 1
            else:
                failed += 1
                print(
                    f"retain failed for {document_id}: "
                    f"{trace.error_code.value} {trace.error_message}",
                    file=sys.stderr,
                )
                if trace.error_code.value == "hindsight_auth_error":
                    print(
                        "authentication failure - stopping so nothing is half-seeded",
                        file=sys.stderr,
                    )
                    break

        _save_state(
            args.state,
            {
                "bank_id": store.bank_id,
                "seeded_at": datetime.now(UTC).isoformat(),
                "document_ids": sorted(done),
            },
        )
        print(f"\nretained={retained} skipped={skipped} failed={failed} total_known={len(done)}")
        return 1 if failed else 0
    finally:
        store.close()


def _reset_bank(store: Any) -> Any:
    """Delete and recreate the bank. Kept separate so --dry-run never reaches it."""
    client = getattr(store, "_client", None)
    if client is None:
        raise RuntimeError("cannot reset a memory store without a provider client")
    try:
        client.delete_bank(bank_id=store.bank_id)
    except Exception as exc:  # noqa: BLE001 - a missing bank is fine
        print(f"delete_bank reported: {exc}")
    return store.create_bank_if_needed()


if __name__ == "__main__":
    raise SystemExit(main())
