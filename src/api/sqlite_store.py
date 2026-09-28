"""SQLite persistence for active incidents and their timelines.

SQLite is the source of truth for incident state (AGENTS.md §11). Hindsight is
the memory layer; it is not asked to store incident records, and this module
never touches it.

The whole ``IncidentResponse`` is stored as one JSON column. That is deliberate:
the contract is the serializer, so the store cannot drift from the schema three
other modules agree on. The indexed columns beside it are written from the same
object in the same call, so they cannot disagree with the JSON either.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from src.contracts import IncidentResponse

logger = logging.getLogger(__name__)

#: Live incidents are numbered from here so they are never confused with the
#: seeded history, which occupies the 10xx range.
LIVE_INCIDENT_START = 9000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id         TEXT PRIMARY KEY,
    -- Indexed copies of the fields AGENTS.md §11 requires, for querying.
    -- Written from the same object as payload_json, in the same call.
    service             TEXT NOT NULL,
    severity            TEXT NOT NULL,
    incident_type       TEXT NOT NULL,
    state               TEXT NOT NULL,
    memory_mode         TEXT NOT NULL,
    operator_outcome    TEXT,
    root_cause_id       TEXT,
    validated_runbook_id TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    -- The contract is the serializer. One column, no field-by-field mapping.
    payload_json        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_created_at ON incidents (created_at);
CREATE INDEX IF NOT EXISTS incidents_service ON incidents (service);
"""


class SqliteIncidentStore:
    """Incident records, durable across restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # A connection per operation. FastAPI runs sync handlers in a threadpool,
        # so a long-lived connection would need `check_same_thread=False` plus a
        # lock, and SQLite serialises writes anyway.
        # ponytail: per-op connect; move to a pooled connection if write volume
        # ever makes connection setup measurable.
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def next_incident_id(self) -> str:
        """The next live incident id, e.g. ``INC-9001``.

        ponytail: read-then-insert, so two simultaneous ``POST /alerts`` could
        race and one would hit the primary key. Fine for a single-operator
        console; use a sequence table if concurrent alert ingestion is added.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(CAST(SUBSTR(incident_id, 5) AS INTEGER)) AS highest "
                "FROM incidents WHERE incident_id LIKE 'INC-9%'"
            ).fetchone()
        highest = row["highest"] if row and row["highest"] is not None else LIVE_INCIDENT_START
        return f"INC-{max(highest + 1, LIVE_INCIDENT_START + 1)}"

    def save(self, incident: IncidentResponse) -> IncidentResponse:
        alert = incident.alert
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO incidents (
                    incident_id, service, severity, incident_type, state, memory_mode,
                    operator_outcome, root_cause_id, validated_runbook_id,
                    created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    service = excluded.service,
                    severity = excluded.severity,
                    incident_type = excluded.incident_type,
                    state = excluded.state,
                    memory_mode = excluded.memory_mode,
                    operator_outcome = excluded.operator_outcome,
                    root_cause_id = excluded.root_cause_id,
                    validated_runbook_id = excluded.validated_runbook_id,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    incident.incident_id,
                    alert.service,
                    alert.severity.value,
                    alert.incident_type.value,
                    incident.state.value,
                    incident.memory_mode.value,
                    incident.operator_outcome.value if incident.operator_outcome else None,
                    incident.root_cause_id,
                    incident.validated_runbook_id,
                    incident.created_at.isoformat(),
                    incident.updated_at.isoformat(),
                    incident.model_dump_json(),
                ),
            )
        return incident

    def get(self, incident_id: str) -> IncidentResponse | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return IncidentResponse.model_validate_json(row["payload_json"])
        except ValueError:
            # A row written by an older contract is a bug, not a 500. Say so and
            # let the caller report the incident as missing.
            logger.exception("incident %s has an unreadable payload", incident_id)
            return None

    def list(self, limit: int = 50) -> list[IncidentResponse]:
        """Newest first, which is the order the alert feed wants."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM incidents ORDER BY created_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()

        incidents: list[IncidentResponse] = []
        for row in rows:
            try:
                incidents.append(IncidentResponse.model_validate_json(row["payload_json"]))
            except ValueError:
                logger.exception("skipping an incident with an unreadable payload")
        return incidents


__all__ = ["LIVE_INCIDENT_START", "SqliteIncidentStore"]
