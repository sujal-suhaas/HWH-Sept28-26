"""Memory layer.

Public surface: :func:`build_memory_store` plus the interface types. Import the
Hindsight SDK only from :mod:`src.memory.hindsight_client`.
"""

from __future__ import annotations

from src.config import Settings
from src.memory.disabled import DisabledMemoryStore
from src.memory.fake import InMemoryMemoryStore
from src.memory.interface import (
    MemoryStore,
    MemoryStoreError,
    RecallHit,
    RecallOutcome,
)
from src.memory.schema import EventType, MemoryEvent, Outcome, runbook_tags
from src.memory.trace import (
    ErrorCode,
    MemoryMode,
    MemoryOperation,
    MemoryTrace,
    TraceLog,
)

__all__ = [
    "DisabledMemoryStore",
    "ErrorCode",
    "EventType",
    "InMemoryMemoryStore",
    "MemoryEvent",
    "MemoryMode",
    "MemoryOperation",
    "MemoryStore",
    "MemoryStoreError",
    "MemoryTrace",
    "Outcome",
    "RecallHit",
    "RecallOutcome",
    "TraceLog",
    "build_memory_store",
    "runbook_tags",
]


def build_memory_store(settings: Settings, *, client: object | None = None) -> MemoryStore:
    """Build the configured memory store.

    ``memory_mode=off`` yields a store that makes no network calls at all.
    """
    if not settings.memory_enabled():
        return DisabledMemoryStore(bank_id=settings.hindsight_bank_id)

    from src.memory.hindsight_client import HindsightMemoryStore

    return HindsightMemoryStore.from_settings(settings, client=client)
