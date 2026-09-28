"""Fixtures for agent tests. No network, no live Hindsight, no live Groq."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.catalog import Catalog, load_catalog
from src.memory.fake import InMemoryMemoryStore
from src.memory.schema import EventType, MemoryEvent, Outcome

SEED_FILES: dict[str, dict[str, Any]] = {
    "services.json": {
        "company": "NimbusPay",
        "services": [
            {
                "name": "checkout-api",
                "tier": 1,
                "owner": "Checkout",
                "dependencies": ["payments-ledger", "redis-cache"],
                "slo": "p99 < 800ms",
            },
            {
                "name": "payments-ledger",
                "tier": 1,
                "owner": "Ledger",
                "dependencies": ["kafka-bus"],
                "slo": "p99 < 300ms",
            },
            {
                "name": "redis-cache",
                "tier": 2,
                "owner": "Platform",
                "dependencies": [],
                "slo": "hit rate > 95%",
            },
            {
                "name": "kafka-bus",
                "tier": 2,
                "owner": "Platform",
                "dependencies": [],
                "slo": "consumer lag < 5000",
            },
        ],
    },
    "runbooks.json": {
        "runbooks": [
            {
                "id": "RB-014",
                "title": "Scale the payments-ledger consumer group and replay the partition",
                "root_cause_id": "RC-014",
                "steps": [
                    "Scale the ledger consumer group from 4 to 12 replicas.",
                    "Replay the affected partitions from the last committed offset.",
                ],
                "verified": True,
            },
            {
                "id": "RB-005",
                "title": "Raise checkout-api connection pool and add a ledger circuit breaker",
                "root_cause_id": "RC-005",
                "steps": [
                    "Raise DB_POOL_SIZE on checkout-api from 20 to 60 and roll "
                    "the deployment."
                ],
                "verified": True,
            },
        ]
    },
    "root_causes.json": {
        "root_causes": [
            {
                "id": "RC-014",
                "name": "kafka_consumer_lag",
                "summary": "Kafka consumer lag on payments-ledger after a deploy",
                "detail": (
                    "A deploy pushed more events through the payments topic than the "
                    "consumer group could drain."
                ),
            },
            {
                "id": "RC-005",
                "name": "connection_pool_exhaustion",
                "summary": "checkout-api connection pool exhausted behind a slow ledger",
                "detail": "Pool waiters queued until requests timed out.",
            },
        ]
    },
}


@pytest.fixture
def catalog(tmp_path: Path) -> Catalog:
    for name, payload in SEED_FILES.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    return load_catalog(tmp_path)


@pytest.fixture
def memory() -> InMemoryMemoryStore:
    return InMemoryMemoryStore(bank_id="dejaops-test")


def seed_lag_incident(store: InMemoryMemoryStore) -> None:
    """One confirmed incident whose resolution points at RB-014."""
    store.retain(
        MemoryEvent(
            event_type=EventType.RESOLUTION,
            incident_id="INC-2201",
            content=(
                "checkout-api latency after payments-ledger deploy. Kafka consumer lag on the "
                "payments topic. Validated fix: scale the ledger consumer group and replay the "
                "affected partition. Runbook RB-014."
            ),
            context="resolution",
            service="checkout-api",
            severity="p1",
            incident_type="latency",
            environment="prod",
            runbook_id="RB-014",
            outcome=Outcome.CONFIRMED,
        )
    )


@pytest.fixture
def memory_with_history() -> InMemoryMemoryStore:
    store = InMemoryMemoryStore(bank_id="dejaops-test")
    seed_lag_incident(store)
    return store
