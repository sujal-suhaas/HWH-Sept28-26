"""Memory-OFF tests: no Hindsight calls, and never a fabricated hit."""

from __future__ import annotations

from src.config import Settings
from src.memory import DisabledMemoryStore, build_memory_store
from src.memory.trace import ErrorCode, MemoryMode, MemoryOperation
from tests.conftest import make_event


class ExplodingClient:
    """Any attribute access is a test failure: memory OFF must not touch Hindsight."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"memory_mode=off must not call Hindsight.{name}")


def test_factory_returns_disabled_store_when_memory_off() -> None:
    settings = Settings(memory_mode="off", hindsight_bank_id="dejaops-prod")
    store = build_memory_store(settings, client=ExplodingClient())
    assert isinstance(store, DisabledMemoryStore)


def test_disabled_recall_reports_memory_off_not_no_match_silently() -> None:
    store = DisabledMemoryStore(bank_id="dejaops-prod")
    outcome = store.recall("checkout latency", tags=["service:checkout-api"])
    assert outcome.hits == []
    assert outcome.no_match is True
    assert outcome.trace.mode is MemoryMode.OFF
    assert outcome.trace.error_code is ErrorCode.MEMORY_OFF
    assert outcome.trace.success is False


def test_disabled_retain_reports_memory_off() -> None:
    store = DisabledMemoryStore(bank_id="dejaops-prod")
    trace = store.retain(make_event())
    assert trace.success is False
    assert trace.error_code is ErrorCode.MEMORY_OFF


def test_disabled_operations_are_all_labelled_off() -> None:
    store = DisabledMemoryStore(bank_id="dejaops-prod")
    for trace in (
        store.create_bank_if_needed(),
        store.health(),
        store.retain(make_event()),
        store.recall("q").trace,
    ):
        assert trace.mode is MemoryMode.OFF
        assert trace.error_code is ErrorCode.MEMORY_OFF


def test_disabled_store_bank_id_is_still_reported() -> None:
    store = DisabledMemoryStore(bank_id="dejaops-prod")
    assert store.bank_id == "dejaops-prod"
    assert store.health().bank_id == "dejaops-prod"
    assert store.health().operation is MemoryOperation.HEALTH
