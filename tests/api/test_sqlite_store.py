"""SQLite store tests. Real file, real round-trip, no network."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.api.sqlite_store import LIVE_INCIDENT_START, SqliteIncidentStore
from src.contracts import FeedbackType, IncidentState
from tests.api.helpers import make_incident


def test_round_trips_every_field(tmp_path: Path) -> None:
    store = SqliteIncidentStore(tmp_path / "dejaops.db")
    incident = make_incident()

    store.save(incident)
    loaded = store.get("INC-9001")

    assert loaded is not None
    # The contract is the serializer, so the whole object must survive.
    assert loaded.model_dump() == incident.model_dump()


def test_save_is_an_upsert(tmp_path: Path) -> None:
    store = SqliteIncidentStore(tmp_path / "dejaops.db")
    incident = make_incident()
    store.save(incident)

    updated = incident.model_copy(
        update={
            "state": IncidentState.RESOLVED,
            "operator_outcome": FeedbackType.RESOLUTION_CONFIRMED,
            "validated_runbook_id": "RB-014",
        }
    )
    store.save(updated)

    loaded = store.get("INC-9001")
    assert loaded is not None
    assert loaded.state is IncidentState.RESOLVED
    assert loaded.validated_runbook_id == "RB-014"
    assert len(store.list()) == 1


def test_get_returns_none_for_an_unknown_incident(tmp_path: Path) -> None:
    store = SqliteIncidentStore(tmp_path / "dejaops.db")
    assert store.get("INC-9999") is None


def test_list_is_newest_first(tmp_path: Path) -> None:
    store = SqliteIncidentStore(tmp_path / "dejaops.db")
    older = make_incident("INC-9001", created_at=datetime(2026, 9, 22, 10, 0, tzinfo=UTC))
    newer = make_incident("INC-9002", created_at=datetime(2026, 9, 22, 11, 0, tzinfo=UTC))
    store.save(older)
    store.save(newer)

    assert [item.incident_id for item in store.list()] == ["INC-9002", "INC-9001"]


def test_list_honours_the_limit(tmp_path: Path) -> None:
    store = SqliteIncidentStore(tmp_path / "dejaops.db")
    for index in range(3):
        store.save(make_incident(f"INC-900{index}"))
    assert len(store.list(limit=2)) == 2


def test_incident_ids_do_not_collide_with_the_seeded_history(tmp_path: Path) -> None:
    store = SqliteIncidentStore(tmp_path / "dejaops.db")
    first = store.next_incident_id()
    assert first == f"INC-{LIVE_INCIDENT_START + 1}"

    store.save(make_incident(first))
    assert store.next_incident_id() == f"INC-{LIVE_INCIDENT_START + 2}"


def test_indexed_columns_match_the_stored_payload(tmp_path: Path) -> None:
    """The denormalised columns are written from the same object as the JSON."""
    store = SqliteIncidentStore(tmp_path / "dejaops.db")
    store.save(make_incident())

    with sqlite3.connect(tmp_path / "dejaops.db") as conn:
        row = conn.execute(
            "SELECT service, severity, incident_type, state, memory_mode FROM incidents"
        ).fetchone()

    assert row == ("checkout-api", "p1", "latency", "WAITING_FOR_OPERATOR", "on")


def test_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "dejaops.db"
    SqliteIncidentStore(path).save(make_incident())
    # A new instance, as after a restart.
    assert SqliteIncidentStore(path).get("INC-9001") is not None


def test_an_unreadable_row_is_skipped_rather_than_crashing(tmp_path: Path) -> None:
    path = tmp_path / "dejaops.db"
    store = SqliteIncidentStore(path)
    store.save(make_incident())
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE incidents SET payload_json = ?", ("{not json",))

    assert store.get("INC-9001") is None
    assert store.list() == []
