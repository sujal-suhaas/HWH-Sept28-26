"""The one and only place the Hindsight SDK is imported.

Everything else in DejaOps depends on :class:`~src.memory.interface.MemoryStore`.
This boundary is what lets us:

* switch memory OFF without touching agent code,
* swap Hindsight Cloud for a self-hosted deployment,
* test memory behaviour with a fake.

Provider failures never raise. They are reported through
:class:`~src.memory.trace.MemoryTrace` so callers can degrade honestly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import threading
import time
from collections.abc import Coroutine
from typing import Any

from hindsight_client import Hindsight

from src.config import Settings
from src.memory.interface import RecallHit, RecallOutcome
from src.memory.schema import MemoryEvent
from src.memory.trace import (
    ErrorCode,
    MemoryMode,
    MemoryOperation,
    MemoryTrace,
    utcnow,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_PERMANENT_STATUS = {400, 401, 403, 404, 409, 422}
_AUTH_STATUS = {401, 403}
_NOT_FOUND_STATUS = {404}
_VALIDATION_STATUS = {400, 422}

_DEFAULT_TAGS_MATCH = "all"
# Measured on the seeded bank: budget/max_tokens bound how many *tokens* come
# back, not how many results. "mid" already returns the full matching set, so a
# larger budget buys nothing and costs latency.
_RECALL_BUDGET = "mid"
_RECALL_MAX_TOKENS = 4096

_BRIDGE_START_TIMEOUT = 10.0

_INCIDENT_ID_RE = re.compile(r"(?:INC|RUNBOOK)-[A-Za-z0-9]+")
_RUNBOOK_ID_RE = re.compile(r"\b(RB-\d+)\b")


def _provenance_rank(hit: RecallHit) -> int:
    """Higher means better provenance. Our retained documents carry metadata."""
    return (2 if hit.metadata else 0) + (1 if hit.document_id else 0)


def _memory_key(hit: RecallHit) -> tuple[str, str]:
    """Identify the memory a hit came from, across Hindsight's derived facts.

    Hindsight returns each retained document twice: once as the document itself
    (``type="world"``, with metadata and a document_id) and once as its own
    paraphrased observation (``type="observation"``, no metadata). Both carry an
    identifier in the text and the event type in the tags, so that pair is the
    stable identity.
    """
    event_type = (hit.event_type or "").lower()

    if event_type == "runbook_entry":
        # A runbook's identity is its runbook id, not the synthetic
        # RUNBOOK-<id> incident id. Resolutions also mention a runbook id in
        # their text, so this branch must stay scoped to runbook entries.
        runbook_id = hit.runbook_id
        if not runbook_id:
            match = _RUNBOOK_ID_RE.search(hit.text)
            runbook_id = match.group(1) if match else hit.text[:48].lower()
        return (f"RUNBOOK-{runbook_id}".upper(), event_type)

    match = _INCIDENT_ID_RE.search(hit.text)
    incident = hit.incident_id or (match.group(0) if match else hit.text[:48].lower())
    return (incident.upper(), event_type)


def _dedupe_hits(hits: list[RecallHit]) -> list[RecallHit]:
    """Keep one hit per memory, preferring the version with provenance.

    ``ponytail:`` collapses multiple chunks of the same document to one. That is
    the right trade for a prompt budget; revisit if chunk-level detail matters.
    """
    best: dict[tuple[str, str], RecallHit] = {}
    for hit in hits:
        key = _memory_key(hit)
        current = best.get(key)
        if current is None or _provenance_rank(hit) > _provenance_rank(current):
            best[key] = hit

    ordered: list[RecallHit] = []
    seen: set[tuple[str, str]] = set()
    for hit in hits:
        key = _memory_key(hit)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(best[key])
    return ordered


def _classify(exc: BaseException) -> tuple[bool, ErrorCode]:
    """Return (retryable, error_code) for a provider exception."""
    status = getattr(exc, "status", None)
    if status is None:
        # Connection/timeout style failure: worth one more try.
        return True, ErrorCode.UNAVAILABLE
    if status in _AUTH_STATUS:
        return False, ErrorCode.AUTH
    if status in _NOT_FOUND_STATUS:
        return False, ErrorCode.NOT_FOUND
    if status in _VALIDATION_STATUS:
        return False, ErrorCode.VALIDATION
    if status in _PERMANENT_STATUS:
        return False, ErrorCode.UNKNOWN
    if status in _RETRYABLE_STATUS or status >= 500:
        return True, ErrorCode.UNAVAILABLE
    return False, ErrorCode.UNKNOWN


class _AsyncBridge:
    """Run coroutines on one dedicated event loop, on one dedicated thread.

    The SDK's synchronous wrappers (``retain``, ``recall``, ...) drive
    ``run_until_complete`` on whatever loop the calling thread happens to have.
    Its own documentation says those wrappers "exist for scripts and REPLs" and
    should not be used from a framework. That matters here because FastAPI
    dispatches every sync handler to a worker thread, where the wrappers fail.

    So the async client is pinned to a single loop on a single thread and every
    call is marshalled onto it. One loop also means one aiohttp connection pool,
    because the SDK builds its session lazily on the loop of the first request.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()

    def _serve(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._loop is None:
                self._ready.clear()
                self._thread = threading.Thread(
                    target=self._serve, name="hindsight-loop", daemon=True
                )
                self._thread.start()
                if not self._ready.wait(timeout=_BRIDGE_START_TIMEOUT):
                    raise RuntimeError("Hindsight event loop thread did not start")
            assert self._loop is not None
            return self._loop

    def run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run ``coro`` to completion and return its result, from any thread."""
        loop = self._ensure_started()
        # run_coroutine_threadsafe wraps the coroutine in a task, which is what
        # asyncio.timeout() inside the SDK requires.
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    def close(self) -> None:
        with self._start_lock:
            loop, thread = self._loop, self._thread
            self._loop, self._thread = None, None
        if loop is None:
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=_BRIDGE_START_TIMEOUT)
        loop.close()


class HindsightMemoryStore:
    """Hindsight-backed :class:`~src.memory.interface.MemoryStore`."""

    mode = MemoryMode.ON

    def __init__(
        self,
        *,
        bank_id: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        min_final_score: float = 0.2,
        client: Any | None = None,
    ) -> None:
        self.bank_id = bank_id
        self.min_final_score = min_final_score
        self._max_retries = max(0, max_retries)
        self._backoff_base = backoff_base_seconds
        self._bridge = _AsyncBridge()
        self._client = client or Hindsight(
            base_url=base_url,
            api_key=api_key or None,
            timeout=timeout_seconds,
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, *, client: Any | None = None
    ) -> HindsightMemoryStore:
        return cls(
            bank_id=settings.hindsight_bank_id,
            base_url=settings.hindsight_base_url,
            api_key=settings.hindsight_api_key.get_secret_value(),
            timeout_seconds=settings.hindsight_timeout_seconds,
            max_retries=settings.hindsight_max_retries,
            backoff_base_seconds=settings.hindsight_backoff_base_seconds,
            min_final_score=settings.hindsight_min_final_score,
            client=client,
        )

    # --- internals ---------------------------------------------------------
    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self._backoff_base * (2**attempt)
        logger.warning("Hindsight call failed; retrying in %.2fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)

    def _call_with_retry(
        self, operation: str, func: Any
    ) -> tuple[Any, int, BaseException | None, ErrorCode]:
        """Run ``func`` with bounded exponential backoff.

        ``func`` returns a fresh coroutine each call, so a retry never re-awaits
        a spent one. Returns ``(result, attempts, last_error, error_code)``.
        Permanent errors are never retried.
        """
        attempts = 0
        last_error: BaseException | None = None
        code = ErrorCode.NONE
        for attempt in range(self._max_retries + 1):
            attempts = attempt + 1
            try:
                return self._bridge.run(func()), attempts, None, ErrorCode.NONE
            except Exception as exc:  # noqa: BLE001 - provider errors are opaque
                retryable, code = _classify(exc)
                last_error = exc
                logger.warning(
                    "Hindsight %s failed (attempt %d/%d, code=%s): %s",
                    operation,
                    attempts,
                    self._max_retries + 1,
                    code.value,
                    exc,
                )
                if not retryable or attempt >= self._max_retries:
                    break
                self._sleep_before_retry(attempt)
        assert last_error is not None
        return None, attempts, last_error, code

    def _failure(
        self,
        operation: MemoryOperation,
        started_at: Any,
        error: BaseException,
        code: ErrorCode,
        attempts: int,
        **extra: Any,
    ) -> MemoryTrace:
        return MemoryTrace.build(
            operation=operation,
            mode=MemoryMode.DEGRADED,
            started_at=started_at,
            success=False,
            bank_id=self.bank_id,
            attempts=attempts,
            degraded=True,
            error_code=code,
            error_message=f"{type(error).__name__}: {error}",
            **extra,
        )

    # --- MemoryStore -------------------------------------------------------
    def create_bank_if_needed(self) -> MemoryTrace:
        started_at = utcnow()
        result, attempts, error, code = self._call_with_retry(
            "create_bank",
            lambda: self._client.acreate_bank(bank_id=self.bank_id, name=self.bank_id),
        )
        if error is not None:
            # The bank may already exist. Verify before reporting failure.
            existing, verify_attempts, verify_error, verify_code = self._call_with_retry(
                "get_bank_config", lambda: self._client.aget_bank_config(bank_id=self.bank_id)
            )
            if verify_error is None:
                return MemoryTrace.build(
                    operation=MemoryOperation.CREATE_BANK,
                    mode=MemoryMode.ON,
                    started_at=started_at,
                    success=True,
                    bank_id=self.bank_id,
                    attempts=attempts + verify_attempts,
                )
            return self._failure(
                MemoryOperation.CREATE_BANK,
                started_at,
                verify_error,
                verify_code or code,
                attempts + verify_attempts,
            )
        return MemoryTrace.build(
            operation=MemoryOperation.CREATE_BANK,
            mode=MemoryMode.ON,
            started_at=started_at,
            success=True,
            bank_id=self.bank_id,
            attempts=attempts,
        )

    def retain(self, event: MemoryEvent) -> MemoryTrace:
        started_at = utcnow()
        tags = event.tags()
        metadata = event.metadata()
        document_id = event.document_id_or_default()

        def _do_retain() -> Any:
            return self._client.aretain(
                bank_id=self.bank_id,
                content=event.content,
                context=event.context,
                timestamp=event.timestamp,
                document_id=document_id,
                metadata=metadata,
                tags=tags,
                retain_async=False,
            )

        result, attempts, error, code = self._call_with_retry("retain", _do_retain)
        if error is not None:
            # A failed authoritative retain must never be reported as success.
            return self._failure(MemoryOperation.RETAIN, started_at, error, code, attempts)

        return MemoryTrace.build(
            operation=MemoryOperation.RETAIN,
            mode=MemoryMode.ON,
            started_at=started_at,
            success=True,
            bank_id=self.bank_id,
            attempts=attempts,
            hit_count=int(getattr(result, "items_count", 0) or 0),
        )

    def recall(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        tags_match: str = _DEFAULT_TAGS_MATCH,
        limit: int = 5,
        min_score: float | None = None,
    ) -> RecallOutcome:
        started_at = utcnow()
        scope_tags = list(tags or [])
        # A similarity threshold is only meaningful over a broad scope. When the
        # tag scope is itself the relevance signal - every runbook validated for
        # one service, for example - a threshold wrongly reports "no match" for
        # memories that are relevant by construction. Measured: RB-014 is the only
        # runbook validated for checkout-api, and it scores 0.0 against the query
        # "checkout-api latency", so the default threshold hid it completely.
        threshold = self.min_final_score if min_score is None else min_score

        def _do_recall() -> Any:
            kwargs: dict[str, Any] = {
                "bank_id": self.bank_id,
                "query": query,
                "budget": _RECALL_BUDGET,
                "max_tokens": _RECALL_MAX_TOKENS,
                "trace": True,
                # Hindsight returns low-relevance neighbours rather than an
                # empty list; this threshold is what makes "no relevant
                # memory found" an honest statement.
                "min_scores": {"final": threshold},
            }
            if scope_tags:
                # tags_match="all" is essential: the SDK default ("any")
                # broadens the scope instead of narrowing it, which silently
                # returns unrelated memory.
                kwargs["tags"] = scope_tags
                kwargs["tags_match"] = tags_match
            return self._client.arecall(**kwargs)

        result, attempts, error, code = self._call_with_retry("recall", _do_recall)
        if error is not None:
            outcome = RecallOutcome(
                hits=[],
                trace=self._failure(
                    MemoryOperation.RECALL,
                    started_at,
                    error,
                    code,
                    attempts,
                    query=query,
                    tags=scope_tags,
                    min_score=threshold,
                    no_match=True,
                ),
            )
            return outcome

        hits = [self._to_hit(item) for item in (getattr(result, "results", None) or [])]
        hits = _dedupe_hits(hits)[:limit]
        provider_trace = getattr(result, "trace", None)
        trace = MemoryTrace.build(
            operation=MemoryOperation.RECALL,
            mode=MemoryMode.ON,
            started_at=started_at,
            success=True,
            bank_id=self.bank_id,
            attempts=attempts,
            hit_count=len(hits),
            query=query,
            tags=scope_tags,
            min_score=threshold,
            no_match=not hits,
            provider_trace=provider_trace,
        )
        return RecallOutcome(hits=hits, trace=trace)

    @staticmethod
    def _to_hit(item: Any) -> RecallHit:
        scores = getattr(item, "scores", None)
        score = getattr(scores, "final", None) if scores is not None else None
        metadata = dict(getattr(item, "metadata", None) or {})
        return RecallHit(
            memory_id=str(getattr(item, "id", "") or ""),
            text=str(getattr(item, "text", "") or ""),
            fact_type=getattr(item, "type", None),
            score=float(score) if score is not None else None,
            tags=list(getattr(item, "tags", None) or []),
            metadata=metadata,
            document_id=getattr(item, "document_id", None),
            context=getattr(item, "context", None),
            occurred_start=getattr(item, "occurred_start", None),
        )

    def health(self) -> MemoryTrace:
        started_at = utcnow()
        result, attempts, error, code = self._call_with_retry(
            "get_version", lambda: self._client.aget_version()
        )
        if error is not None:
            return self._failure(MemoryOperation.HEALTH, started_at, error, code, attempts)
        return MemoryTrace.build(
            operation=MemoryOperation.HEALTH,
            mode=MemoryMode.ON,
            started_at=started_at,
            success=True,
            bank_id=self.bank_id,
            attempts=attempts,
            provider_trace={
                "api_version": str(getattr(result, "api_version", "")),
                "base_url": "configured",
            },
        )

    def delete_bank(self) -> bool:
        """Delete the bank. Offline seeding only, so it produces no trace.

        This exists so nothing outside this module ever touches the provider
        client: the SDK's sync wrappers bind to the calling thread, and a raw
        call from a thread the adapter does not own poisons every later call.
        """
        try:
            self._bridge.run(self._client.adelete_bank(bank_id=self.bank_id))
        except Exception as exc:  # noqa: BLE001 - a missing bank is fine
            logger.warning("Hindsight delete_bank failed: %s", exc)
            return False
        return True

    def close(self) -> None:
        try:
            self._bridge.run(self._client.aclose())
        except Exception as exc:  # noqa: BLE001
            logger.warning("closing Hindsight client failed: %s", exc)
        finally:
            self._bridge.close()
