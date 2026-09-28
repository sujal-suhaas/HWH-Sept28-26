"""Adapter tests against a fake Hindsight client.

These pin the behaviours AGENTS.md §12/§13 requires: bounded retry, no blind
retry of auth errors, honest degradation, tags used for scoping, metadata
carried as context, and an explicit empty-recall signal.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.memory.hindsight_client import HindsightMemoryStore
from src.memory.interface import RecallHit
from src.memory.trace import ErrorCode, MemoryMode
from tests.conftest import make_event, recall_item


def store_hit(item: Any) -> RecallHit:
    """Normalize a fake provider result the same way the adapter does."""
    return HindsightMemoryStore._to_hit(item)


class ApiError(Exception):
    """Stands in for hindsight_client_api.ApiException."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class FakeHindsight:
    """Records calls and replays scripted outcomes."""

    def __init__(
        self,
        *,
        retain_errors: list[Exception] | None = None,
        recall_errors: list[Exception] | None = None,
        create_bank_errors: list[Exception] | None = None,
        bank_config_errors: list[Exception] | None = None,
        delete_bank_errors: list[Exception] | None = None,
        recall_results: list[Any] | None = None,
        recall_trace: dict[str, Any] | None = None,
    ) -> None:
        self.retain_errors = list(retain_errors or [])
        self.recall_errors = list(recall_errors or [])
        self.create_bank_errors = list(create_bank_errors or [])
        self.bank_config_errors = list(bank_config_errors or [])
        self.delete_bank_errors = list(delete_bank_errors or [])
        self.recall_results = recall_results if recall_results is not None else [recall_item()]
        self.recall_trace = recall_trace or {"summary": {"results_returned": 1}}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def _maybe_fail(self, errors: list[Exception]) -> None:
        if errors:
            raise errors.pop(0)

    async def acreate_bank(self, **kwargs: Any) -> Any:
        self.calls.append(("create_bank", kwargs))
        self._maybe_fail(self.create_bank_errors)
        return SimpleNamespace(bank_id=kwargs.get("bank_id"))

    async def aget_bank_config(self, **kwargs: Any) -> Any:
        self.calls.append(("get_bank_config", kwargs))
        self._maybe_fail(self.bank_config_errors)
        return {"bank_id": kwargs.get("bank_id")}

    async def aretain(self, **kwargs: Any) -> Any:
        self.calls.append(("retain", kwargs))
        self._maybe_fail(self.retain_errors)
        return SimpleNamespace(success=True, bank_id=kwargs.get("bank_id"), items_count=1)

    async def arecall(self, **kwargs: Any) -> Any:
        self.calls.append(("recall", kwargs))
        self._maybe_fail(self.recall_errors)
        return SimpleNamespace(results=self.recall_results, trace=self.recall_trace)

    async def adelete_bank(self, **kwargs: Any) -> Any:
        self.calls.append(("delete_bank", kwargs))
        self._maybe_fail(self.delete_bank_errors)
        return SimpleNamespace(success=True)

    async def aget_version(self) -> Any:
        self.calls.append(("get_version", {}))
        return SimpleNamespace(api_version="0.10.1")

    async def aclose(self) -> None:
        self.closed = True
    def calls_named(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for call, kwargs in self.calls if call == name]


def make_store(client: FakeHindsight, **overrides: Any) -> HindsightMemoryStore:
    params: dict[str, Any] = {
        "bank_id": "dejaops-prod",
        "base_url": "https://example.invalid",
        "api_key": "k",
        "max_retries": 3,
        "backoff_base_seconds": 0.0,
        "min_final_score": 0.2,
        "client": client,
    }
    params.update(overrides)
    return HindsightMemoryStore(**params)


# --- retain ----------------------------------------------------------------
def test_retain_sends_scoping_tags_and_context_metadata_separately() -> None:
    client = FakeHindsight()
    store = make_store(client)
    event = make_event()

    trace = store.retain(event)

    assert trace.success is True
    assert trace.mode is MemoryMode.ON
    kwargs = client.calls_named("retain")[0]
    assert "service:checkout-api" in kwargs["tags"]
    assert "event_type:postmortem" in kwargs["tags"]
    # metadata carries context and must not be used as the filter
    assert kwargs["metadata"]["incident_id"] == "INC-1001"
    assert kwargs["metadata"]["runbook_id"] == "RB-014"
    assert not set(kwargs["metadata"]) & set(kwargs["tags"])
    assert kwargs["document_id"] == "INC-1001:postmortem"


def test_retain_retries_transient_failure_then_succeeds() -> None:
    client = FakeHindsight(retain_errors=[ApiError(503)])
    store = make_store(client)

    trace = store.retain(make_event())

    assert trace.success is True
    assert trace.attempts == 2


def test_retain_does_not_retry_auth_failure() -> None:
    client = FakeHindsight(retain_errors=[ApiError(401), ApiError(401), ApiError(401)])
    store = make_store(client)

    trace = store.retain(make_event())

    assert trace.success is False
    assert trace.attempts == 1
    assert trace.error_code is ErrorCode.AUTH
    assert trace.degraded is True
    assert len(client.calls_named("retain")) == 1


def test_failed_retain_is_never_reported_as_success() -> None:
    client = FakeHindsight(retain_errors=[ApiError(500)] * 10)
    store = make_store(client)

    trace = store.retain(make_event())

    assert trace.success is False
    assert trace.error_code is ErrorCode.UNAVAILABLE
    assert trace.degraded is True
    assert len(client.calls_named("retain")) == 4  # 1 + 3 retries, then stop


def test_retain_retries_connection_errors_without_status() -> None:
    client = FakeHindsight(retain_errors=[ConnectionError("reset by peer")])
    store = make_store(client)

    trace = store.retain(make_event())

    assert trace.success is True
    assert trace.attempts == 2


# --- recall ----------------------------------------------------------------
def test_recall_normalizes_hits_and_applies_score_threshold() -> None:
    client = FakeHindsight(recall_results=[recall_item(final_score=0.91)])
    store = make_store(client)

    outcome = store.recall("checkout latency", tags=["service:checkout-api"])

    assert outcome.no_match is False
    assert len(outcome.hits) == 1
    hit = outcome.hits[0]
    assert hit.memory_id == "m-1"
    assert hit.score == pytest.approx(0.91)
    assert hit.incident_id == "INC-1001"
    assert hit.event_type == "POSTMORTEM"
    kwargs = client.calls_named("recall")[0]
    assert kwargs["tags"] == ["service:checkout-api"]
    assert kwargs["min_scores"] == {"final": 0.2}


def test_recall_threshold_is_configurable() -> None:
    client = FakeHindsight()
    store = make_store(client, min_final_score=0.55)

    store.recall("checkout latency", tags=["service:checkout-api"])

    assert client.calls_named("recall")[0]["min_scores"] == {"final": 0.55}
def test_recall_returns_explicit_no_match_when_provider_is_empty() -> None:
    client = FakeHindsight(recall_results=[])
    store = make_store(client)

    outcome = store.recall("checkout latency", tags=["service:checkout-api"])

    assert outcome.hits == []
    assert outcome.no_match is True
    assert outcome.trace.success is True
    assert outcome.trace.no_match is True
    assert outcome.trace.hit_count == 0


def test_recall_provider_trace_is_sanitized_of_embeddings() -> None:
    client = FakeHindsight(
        recall_trace={
            "query": {"query_text": "checkout latency", "query_embedding": [0.1] * 384},
            "summary": {"results_returned": 1},
        }
    )
    store = make_store(client)

    outcome = store.recall("checkout latency")

    assert outcome.trace.provider_trace is not None
    assert "query_embedding" not in outcome.trace.provider_trace["query"]
    assert outcome.trace.provider_trace["summary"]["results_returned"] == 1


def test_recall_degrades_after_retry_exhaustion_and_fabricates_nothing() -> None:
    client = FakeHindsight(recall_errors=[ApiError(503)] * 10)
    store = make_store(client)

    outcome = store.recall("checkout latency", tags=["service:checkout-api"])

    assert outcome.hits == []
    assert outcome.no_match is True
    assert outcome.trace.success is False
    assert outcome.trace.degraded is True
    assert outcome.trace.mode is MemoryMode.DEGRADED
    assert outcome.trace.error_code is ErrorCode.UNAVAILABLE
    assert outcome.trace.tags == ["service:checkout-api"]


def test_recall_does_not_retry_auth_failure() -> None:
    client = FakeHindsight(recall_errors=[ApiError(403)] * 5)
    store = make_store(client)

    outcome = store.recall("checkout latency")

    assert outcome.trace.error_code is ErrorCode.AUTH
    assert outcome.trace.attempts == 1
    assert len(client.calls_named("recall")) == 1


def test_recall_applies_client_side_limit() -> None:
    client = FakeHindsight(
        recall_results=[
            recall_item(
                memory_id=f"m-{i}",
                text=f"checkout-api latency incident INC-20{i:02d} after a ledger deploy",
                metadata={"incident_id": f"INC-20{i:02d}", "event_type": "POSTMORTEM"},
            )
            for i in range(10)
        ]
    )
    store = make_store(client)

    outcome = store.recall("checkout latency", limit=3)

    assert len(outcome.hits) == 3
    assert outcome.trace.hit_count == 3


def test_recall_dedupes_a_document_against_its_derived_observation() -> None:
    """Hindsight returns each retained document twice: once as the document, once as
    its own paraphrase with no metadata. Only one copy should reach the caller, and
    it should be the one that carries provenance."""
    document = recall_item(
        memory_id="doc-1",
        text="Incident INC-1038 opened for checkout-api due to high p99 latency.",
        metadata={"incident_id": "INC-1038", "event_type": "INCIDENT_OPEN"},
    )
    observation = recall_item(
        memory_id="obs-1",
        text="Incident INC-1038 was opened for checkout-api due to high p99 latency.",
        metadata={},
        tags=["service:checkout-api", "event_type:incident_open"],
    )
    observation.document_id = None

    # The derived observation ranks first here; the document must still win.
    client = FakeHindsight(recall_results=[observation, document])
    store = make_store(client)

    outcome = store.recall("checkout latency")

    assert len(outcome.hits) == 1
    assert outcome.hits[0].memory_id == "doc-1"
    assert outcome.hits[0].metadata


def test_recall_keeps_distinct_event_types_for_the_same_incident() -> None:
    client = FakeHindsight(
        recall_results=[
            recall_item(
                memory_id="open-1",
                text="Incident INC-1038 opened for checkout-api due to high p99 latency.",
                metadata={"incident_id": "INC-1038", "event_type": "INCIDENT_OPEN"},
            ),
            recall_item(
                memory_id="res-1",
                text="Incident INC-1038 was resolved by scaling the consumer group.",
                metadata={"incident_id": "INC-1038", "event_type": "RESOLUTION"},
            ),
        ]
    )
    store = make_store(client)

    outcome = store.recall("checkout latency")

    assert {hit.memory_id for hit in outcome.hits} == {"open-1", "res-1"}


def test_recall_keeps_unattributed_memories() -> None:
    """Hindsight's own derived memories are legitimate memory even without provenance."""
    observation = recall_item(memory_id="obs-1", text="Latency after a ledger deploy.", metadata={})
    observation.document_id = None
    client = FakeHindsight(recall_results=[observation])
    store = make_store(client)

    outcome = store.recall("checkout latency")

    assert len(outcome.hits) == 1
    assert outcome.hits[0].metadata == {}


def test_event_type_falls_back_to_the_scoping_tag() -> None:
    """Derived observations lose metadata but keep the tags of their source memory."""
    observation = recall_item(memory_id="obs-1", metadata={}, tags=["event_type:resolution"])
    observation.document_id = None
    hit = store_hit(observation)
    assert hit.event_type == "resolution"


def test_metadata_event_type_wins_over_the_tag() -> None:
    item = recall_item(metadata={"event_type": "POSTMORTEM"}, tags=["event_type:postmortem"])
    assert store_hit(item).event_type == "POSTMORTEM"


def test_recall_dedupes_a_runbook_against_its_derived_observation() -> None:
    """Runbook memories are identified by runbook id, not by the synthetic incident id."""
    document = recall_item(
        memory_id="rb-doc",
        text=(
            "Validated runbook RB-014 for root cause RC-001 to resolve payment ledger issues "
            "by scaling the consumer group."
        ),
        metadata={
            "incident_id": "RUNBOOK-RB-014",
            "event_type": "RUNBOOK_ENTRY",
            "runbook_id": "RB-014",
        },
        tags=["service:checkout-api", "event_type:runbook_entry"],
    )
    observation = recall_item(
        memory_id="rb-obs",
        text=(
            "Validated runbook RB-014 for root cause RC-001 to resolve payment ledger issues "
            "by scaling the consumer group."
        ),
        metadata={},
        tags=["service:checkout-api", "event_type:runbook_entry"],
    )
    observation.document_id = None

    client = FakeHindsight(recall_results=[observation, document])
    store = make_store(client)

    outcome = store.recall("checkout latency")

    assert len(outcome.hits) == 1
    assert outcome.hits[0].memory_id == "rb-doc"


def test_a_resolution_mentioning_a_runbook_is_not_confused_with_the_runbook() -> None:
    resolution = recall_item(
        memory_id="res-1",
        text="Incident INC-1024 was resolved following runbook RB-014.",
        metadata={"incident_id": "INC-1024", "event_type": "RESOLUTION"},
        tags=["event_type:resolution"],
    )
    runbook = recall_item(
        memory_id="rb-doc",
        text="Validated runbook RB-014 for root cause RC-001.",
        metadata={"incident_id": "RUNBOOK-RB-014", "event_type": "RUNBOOK_ENTRY"},
        tags=["event_type:runbook_entry"],
    )
    client = FakeHindsight(recall_results=[resolution, runbook])
    store = make_store(client)

    outcome = store.recall("checkout latency")

    assert {hit.memory_id for hit in outcome.hits} == {"res-1", "rb-doc"}


def test_recall_defaults_to_all_tag_matching() -> None:
    """The SDK default of "any" broadens a scope instead of narrowing it."""
    client = FakeHindsight()
    store = make_store(client)

    store.recall("checkout latency", tags=["service:checkout-api", "event_type:resolution"])

    kwargs = client.calls_named("recall")[0]
    assert kwargs["tags_match"] == "all"


def test_recall_without_tags_sends_no_tag_filter_at_all() -> None:
    client = FakeHindsight()
    store = make_store(client)

    store.recall("checkout latency")

    kwargs = client.calls_named("recall")[0]
    assert "tags" not in kwargs
    assert "tags_match" not in kwargs


def test_recall_uses_the_configured_threshold_by_default() -> None:
    client = FakeHindsight()
    store = make_store(client)

    store.recall("checkout latency")

    kwargs = client.calls_named("recall")[0]
    assert kwargs["min_scores"] == {"final": store.min_final_score}


def test_recall_min_score_override_reaches_the_provider_and_the_trace() -> None:
    """An exact tag scope is its own relevance signal, so the caller can opt out."""
    client = FakeHindsight()
    store = make_store(client)

    outcome = store.recall(
        "checkout latency", tags=["service:checkout-api"], min_score=0.0
    )

    kwargs = client.calls_named("recall")[0]
    assert kwargs["min_scores"] == {"final": 0.0}
    assert outcome.trace.min_score == 0.0


def test_recall_omits_tag_filter_when_scope_is_empty() -> None:
    client = FakeHindsight()
    store = make_store(client)

    store.recall("checkout latency", tags=[])

    kwargs = client.calls_named("recall")[0]
    assert "tags" not in kwargs
    assert "tags_match" not in kwargs


# --- bank + health ---------------------------------------------------------
def test_create_bank_succeeds_on_first_try() -> None:
    client = FakeHindsight()
    store = make_store(client)

    trace = store.create_bank_if_needed()

    assert trace.success is True
    assert trace.attempts == 1


def test_create_bank_treats_existing_bank_as_success() -> None:
    client = FakeHindsight(create_bank_errors=[ApiError(409)])
    store = make_store(client)

    trace = store.create_bank_if_needed()

    assert trace.success is True
    assert len(client.calls_named("get_bank_config")) == 1


def test_create_bank_reports_failure_when_bank_missing_and_create_fails() -> None:
    client = FakeHindsight(
        create_bank_errors=[ApiError(500)] * 5,
        bank_config_errors=[ApiError(404)],
    )
    store = make_store(client)

    trace = store.create_bank_if_needed()

    assert trace.success is False
    assert trace.error_code is ErrorCode.NOT_FOUND
    assert trace.degraded is True


def test_health_reports_version_on_success() -> None:
    client = FakeHindsight()
    store = make_store(client)

    trace = store.health()

    assert trace.success is True
    assert trace.provider_trace == {"api_version": "0.10.1", "base_url": "configured"}


def test_close_is_forwarded() -> None:
    client = FakeHindsight()
    store = make_store(client)

    store.close()

    assert client.closed is True


def test_calls_succeed_from_a_worker_thread_while_a_loop_is_running() -> None:
    """The FastAPI shape: a sync handler on a worker thread, loop elsewhere.

    The SDK's sync wrappers drive ``run_until_complete`` on the caller's loop,
    which raises as soon as FastAPI dispatches a handler to a worker thread.
    Regression guard for every memory call the API makes.
    """

    async def exercise() -> tuple[bool, bool, int, bool]:
        client = FakeHindsight()
        store = make_store(client)
        bank = await asyncio.to_thread(store.create_bank_if_needed)
        retained = await asyncio.to_thread(store.retain, make_event())
        recalled = await asyncio.to_thread(store.recall, "checkout latency")
        await asyncio.to_thread(store.close)
        return bank.success, retained.success, len(recalled.hits), client.closed

    bank_ok, retain_ok, hits, closed = asyncio.run(exercise())

    assert (bank_ok, retain_ok, hits, closed) == (True, True, 1, True)


def test_a_retry_after_a_failure_still_works() -> None:
    """A retry must build a fresh coroutine; a spent one cannot be re-awaited."""
    client = FakeHindsight(retain_errors=[ApiError(503)])
    store = make_store(client)

    trace = store.retain(make_event())

    assert trace.success is True
    assert trace.attempts == 2


def test_delete_bank_is_forwarded_and_reports_failure() -> None:
    client = FakeHindsight()
    store = make_store(client)

    assert store.delete_bank() is True
    assert len(client.calls_named("delete_bank")) == 1

    failing = FakeHindsight(delete_bank_errors=[ApiError(500)])
    assert make_store(failing).delete_bank() is False


def test_error_message_does_not_contain_the_api_key() -> None:
    client = FakeHindsight(retain_errors=[ApiError(401)])
    store = make_store(client, api_key="super-secret-key")

    trace = store.retain(make_event())

    assert trace.error_message is not None
    assert "super-secret-key" not in trace.error_message
