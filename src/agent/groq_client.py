"""Groq client with retry, model fallback, and an honest failure path.

Retry policy:

* retryable (429, 5xx, timeout, connection): bounded exponential backoff, then
  fall back to the configured fallback model.
* permanent (400, 401, 403, 404, 422): never retried. A primary model that no
  longer exists fails clearly instead of silently degrading to another model,
  because a silent model swap would change what the demo actually ran on.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from groq import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    Groq,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from src.agent.trace import ModelCall, ModelRole
from src.config import Settings
from src.memory.trace import utcnow

logger = logging.getLogger(__name__)

DEFAULT_MAX_COMPLETION_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.1


class ModelErrorCode(StrEnum):
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    AUTH = "auth"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_REQUEST = "invalid_request"
    UNKNOWN = "unknown"


_RETRYABLE_CODES = frozenset(
    {
        ModelErrorCode.RATE_LIMIT,
        ModelErrorCode.UNAVAILABLE,
        ModelErrorCode.TIMEOUT,
        ModelErrorCode.CONNECTION,
    }
)


class ModelCallError(Exception):
    """One failed attempt against one model."""

    def __init__(self, code: ModelErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_CODES

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"


class AllModelsFailedError(Exception):
    """Every configured model failed. The caller must surface an honest error."""

    def __init__(self, message: str, errors: list[ModelCallError] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []

    def codes(self) -> list[str]:
        return [error.code.value for error in self.errors]


@dataclass
class ToolCallRequest:
    """A tool call exactly as the model emitted it, still untrusted."""

    id: str
    name: str
    arguments: str


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCallRequest]
    model: str
    role: ModelRole
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    latency_ms: float = 0.0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class _AttemptResult:
    response: ModelResponse | None = None
    fatal: ModelCallError | None = None
    errors: list[ModelCallError] = field(default_factory=list)


def classify_model_error(exc: BaseException) -> ModelCallError:
    """Map a provider exception onto our own error taxonomy."""
    if isinstance(exc, RateLimitError):
        return ModelCallError(ModelErrorCode.RATE_LIMIT, str(exc))
    if isinstance(exc, APITimeoutError):
        return ModelCallError(ModelErrorCode.TIMEOUT, str(exc))
    if isinstance(exc, APIConnectionError):
        return ModelCallError(ModelErrorCode.CONNECTION, str(exc))
    if isinstance(exc, AuthenticationError | PermissionDeniedError):
        return ModelCallError(ModelErrorCode.AUTH, str(exc))
    if isinstance(exc, NotFoundError):
        return ModelCallError(ModelErrorCode.MODEL_NOT_FOUND, str(exc))
    if isinstance(exc, BadRequestError | UnprocessableEntityError):
        return ModelCallError(ModelErrorCode.INVALID_REQUEST, str(exc))
    if isinstance(exc, InternalServerError):
        return ModelCallError(ModelErrorCode.UNAVAILABLE, str(exc))
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        if status >= 500:
            return ModelCallError(ModelErrorCode.UNAVAILABLE, str(exc))
        return ModelCallError(ModelErrorCode.UNKNOWN, str(exc))
    if isinstance(exc, APIError):
        # Base provider error, e.g. a malformed response body. Transient enough
        # to be worth one retry.
        return ModelCallError(ModelErrorCode.UNAVAILABLE, str(exc))
    return ModelCallError(ModelErrorCode.UNKNOWN, f"{type(exc).__name__}: {exc}")


class GroqChatClient:
    """Thin wrapper over the Groq chat completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        primary_model: str,
        fallback_model: str | None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.5,
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        client: Any | None = None,
    ) -> None:
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self._max_retries = max(0, max_retries)
        self._backoff_base = backoff_base_seconds
        self._max_completion_tokens = max_completion_tokens
        self._temperature = temperature
        self._client = client or Groq(api_key=api_key or None, timeout=timeout_seconds)

    @classmethod
    def from_settings(cls, settings: Settings, *, client: Any | None = None) -> GroqChatClient:
        return cls(
            api_key=settings.groq_api_key.get_secret_value(),
            primary_model=settings.groq_model_primary,
            fallback_model=settings.groq_model_fallback,
            timeout_seconds=settings.groq_timeout_seconds,
            max_retries=settings.groq_max_retries,
            backoff_base_seconds=settings.groq_backoff_base_seconds,
            client=client,
        )

    # --- model availability -------------------------------------------------
    def list_models(self) -> set[str]:
        return {model.id for model in self._client.models.list().data}

    def verify_models(self) -> list[str]:
        """Fail clearly if a configured model is not available.

        Returns the list of configured models that were verified.
        """
        available = self.list_models()
        configured = [self.primary_model]
        if self.fallback_model:
            configured.append(self.fallback_model)
        missing = [model for model in configured if model not in available]
        if missing:
            raise ModelCallError(
                ModelErrorCode.MODEL_NOT_FOUND,
                f"configured model(s) not available on this provider: {missing}. "
                f"Available: {sorted(available)}",
            )
        return configured

    # --- calls --------------------------------------------------------------
    def _invoke(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Any:
        return self._client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=self._temperature,
            max_completion_tokens=self._max_completion_tokens,
        )

    def _attempt_model(
        self,
        model: str,
        role: ModelRole,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        record: Callable[[ModelCall], None],
    ) -> _AttemptResult:
        errors: list[ModelCallError] = []
        total_attempts = self._max_retries + 1

        for attempt in range(1, total_attempts + 1):
            started_at = utcnow()
            started = time.perf_counter()
            call = ModelCall(model=model, role=role, attempt=attempt, started_at=started_at)
            try:
                raw = self._invoke(model, messages, tools)
                choice = raw.choices[0]
                message = choice.message
                tool_calls = [
                    ToolCallRequest(
                        id=item.id,
                        name=item.function.name,
                        arguments=item.function.arguments or "",
                    )
                    for item in (message.tool_calls or [])
                ]
                call.ok = True
                call.finish_reason = choice.finish_reason
                call.tool_call_count = len(tool_calls)
                call.usage = self._usage(raw)
                call.latency_ms = round((time.perf_counter() - started) * 1000, 2)
                record(call)
                return _AttemptResult(
                    response=ModelResponse(
                        text=message.content,
                        tool_calls=tool_calls,
                        model=model,
                        role=role,
                        finish_reason=choice.finish_reason,
                        usage=call.usage,
                        latency_ms=call.latency_ms,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - provider errors are opaque
                error = classify_model_error(exc)
                call.error_code = error.code.value
                call.error_message = error.message
                call.latency_ms = round((time.perf_counter() - started) * 1000, 2)
                record(call)
                errors.append(error)

                if not error.retryable:
                    logger.warning("model %s failed permanently: %s", model, error)
                    return _AttemptResult(fatal=error, errors=errors)

                logger.warning(
                    "model %s attempt %d/%d failed (%s)", model, attempt, total_attempts, error
                )
                if attempt < total_attempts:
                    delay = self._backoff_base * (2 ** (attempt - 1))
                    time.sleep(delay)

        return _AttemptResult(errors=errors)

    @staticmethod
    def _usage(raw: Any) -> dict[str, Any] | None:
        usage = getattr(raw, "usage", None)
        if usage is None:
            return None
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        record: Callable[[ModelCall], None] | None = None,
    ) -> ModelResponse:
        """Call the primary model, falling back only on retryable failures."""
        recorder = record or (lambda call: None)
        all_errors: list[ModelCallError] = []

        primary = self._attempt_model(
            self.primary_model, ModelRole.PRIMARY, messages, tools, recorder
        )
        if primary.response is not None:
            return primary.response
        if primary.fatal is not None:
            # A permanently broken primary is a configuration failure, not
            # something to paper over with a different model.
            raise AllModelsFailedError(
                f"primary model '{self.primary_model}' failed permanently: {primary.fatal}",
                primary.errors,
            )
        all_errors.extend(primary.errors)

        if not self.fallback_model:
            raise AllModelsFailedError(
                f"primary model '{self.primary_model}' exhausted retries and no fallback is "
                f"configured",
                all_errors,
            )

        logger.warning(
            "falling back from %s to %s after %d failed attempt(s)",
            self.primary_model,
            self.fallback_model,
            len(primary.errors),
        )
        fallback = self._attempt_model(
            self.fallback_model, ModelRole.FALLBACK, messages, tools, recorder
        )
        if fallback.response is not None:
            return fallback.response
        all_errors.extend(fallback.errors)

        raise AllModelsFailedError(
            f"all configured models failed ({self.primary_model}, {self.fallback_model}): "
            + "; ".join(str(error) for error in all_errors),
            all_errors,
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("closing Groq client failed: %s", exc)
