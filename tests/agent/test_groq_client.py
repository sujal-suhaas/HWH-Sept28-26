"""Groq client tests: retry, fallback, and the honest failure path.

No network. The provider is replaced with a scripted fake, so the retry and
fallback behaviour is asserted deterministically.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from groq import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from src.agent.groq_client import (
    AllModelsFailedError,
    GroqChatClient,
    ModelCallError,
    ModelErrorCode,
    classify_model_error,
)
from src.agent.trace import ModelRole
from src.config import Settings
from tests.agent.fake_llm import FALLBACK, PRIMARY

_REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_REQUEST)


# --------------------------------------------------------------------------
# Fake provider
# --------------------------------------------------------------------------
class FakeCompletions:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("fake provider script exhausted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeGroqClient:
    def __init__(self, script: list[Any], models: list[str] | None = None) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(script))
        self._models = models if models is not None else [PRIMARY, FALLBACK]
        self.closed = False

    @property
    def models(self) -> Any:
        data = [SimpleNamespace(id=name) for name in self._models]
        return SimpleNamespace(list=lambda: SimpleNamespace(data=data))

    def close(self) -> None:
        self.closed = True


def completion(
    content: str | None = "ok",
    *,
    tool_calls: tuple[Any, ...] = (),
    finish_reason: str = "stop",
) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=list(tool_calls)),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )


def tool_call(name: str, arguments: str, call_id: str = "call_1") -> Any:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def make_client(
    script: list[Any],
    *,
    models: list[str] | None = None,
    fallback: str | None = FALLBACK,
    max_retries: int = 2,
) -> tuple[GroqChatClient, FakeGroqClient]:
    fake = FakeGroqClient(script, models=models)
    client = GroqChatClient(
        api_key="test",
        primary_model=PRIMARY,
        fallback_model=fallback,
        max_retries=max_retries,
        backoff_base_seconds=0.0,
        client=fake,
    )
    return client, fake


# --------------------------------------------------------------------------
# Error classification
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (
            RateLimitError("429", response=_response(429), body=None),
            ModelErrorCode.RATE_LIMIT,
            True,
        ),
        (APITimeoutError(request=_REQUEST), ModelErrorCode.TIMEOUT, True),
        (APIConnectionError(request=_REQUEST), ModelErrorCode.CONNECTION, True),
        (
            InternalServerError("500", response=_response(500), body=None),
            ModelErrorCode.UNAVAILABLE,
            True,
        ),
        (
            AuthenticationError("401", response=_response(401), body=None),
            ModelErrorCode.AUTH,
            False,
        ),
        (
            PermissionDeniedError("403", response=_response(403), body=None),
            ModelErrorCode.AUTH,
            False,
        ),
        (
            NotFoundError("404", response=_response(404), body=None),
            ModelErrorCode.MODEL_NOT_FOUND,
            False,
        ),
        (
            BadRequestError("400", response=_response(400), body=None),
            ModelErrorCode.INVALID_REQUEST,
            False,
        ),
        (
            UnprocessableEntityError("422", response=_response(422), body=None),
            ModelErrorCode.INVALID_REQUEST,
            False,
        ),
    ],
)
def test_error_classification(error: BaseException, code: ModelErrorCode, retryable: bool) -> None:
    classified = classify_model_error(error)
    assert classified.code is code
    assert classified.retryable is retryable


def test_unknown_error_is_not_retried() -> None:
    classified = classify_model_error(RuntimeError("boom"))
    assert classified.code is ModelErrorCode.UNKNOWN
    assert classified.retryable is False


def test_model_call_error_str_includes_the_code() -> None:
    assert str(ModelCallError(ModelErrorCode.AUTH, "nope")) == "auth: nope"


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------
def test_success_on_the_first_attempt_makes_one_call() -> None:
    client, fake = make_client([completion("hello")])
    response = client.complete([{"role": "user", "content": "hi"}], [])
    assert response.text == "hello"
    assert response.role is ModelRole.PRIMARY
    assert len(fake.chat.completions.calls) == 1


def test_retryable_failure_is_retried_then_succeeds() -> None:
    client, fake = make_client(
        [RateLimitError("429", response=_response(429), body=None), completion("recovered")]
    )
    recorded: list[Any] = []
    response = client.complete([{"role": "user", "content": "hi"}], [], record=recorded.append)

    assert response.text == "recovered"
    assert response.role is ModelRole.PRIMARY
    assert len(fake.chat.completions.calls) == 2
    assert [call.ok for call in recorded] == [False, True]
    assert recorded[0].error_code == "rate_limit"
    assert recorded[0].attempt == 1
    assert recorded[1].attempt == 2


def test_backoff_is_applied_between_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("src.agent.groq_client.time.sleep", slept.append)

    fake = FakeGroqClient(
        [
            RateLimitError("429", response=_response(429), body=None),
            RateLimitError("429", response=_response(429), body=None),
            completion(),
        ]
    )
    client = GroqChatClient(
        api_key="test",
        primary_model=PRIMARY,
        fallback_model=FALLBACK,
        max_retries=2,
        backoff_base_seconds=0.5,
        client=fake,
    )
    client.complete([{"role": "user", "content": "hi"}], [])
    assert slept == [0.5, 1.0]


# --------------------------------------------------------------------------
# Fallback
# --------------------------------------------------------------------------
def test_exhausted_primary_retries_fall_back_to_the_fallback_model() -> None:
    client, fake = make_client(
        [
            RateLimitError("429", response=_response(429), body=None),
            RateLimitError("429", response=_response(429), body=None),
            completion("from fallback"),
        ],
        max_retries=1,
    )
    recorded: list[Any] = []
    response = client.complete([{"role": "user", "content": "hi"}], [], record=recorded.append)

    assert response.text == "from fallback"
    assert response.role is ModelRole.FALLBACK
    assert response.model == FALLBACK
    models_called = [call["model"] for call in fake.chat.completions.calls]
    assert models_called == [PRIMARY, PRIMARY, FALLBACK]
    assert [call.role for call in recorded] == [
        ModelRole.PRIMARY,
        ModelRole.PRIMARY,
        ModelRole.FALLBACK,
    ]


def test_a_permanently_broken_primary_does_not_silently_fall_back() -> None:
    """A decommissioned model must fail clearly, not quietly swap models."""
    client, fake = make_client([NotFoundError("404", response=_response(404), body=None)])

    with pytest.raises(AllModelsFailedError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}], [])

    assert "failed permanently" in str(excinfo.value)
    assert excinfo.value.codes() == ["model_not_found"]
    # Only one call: the fallback was never attempted.
    assert len(fake.chat.completions.calls) == 1


def test_a_permanent_primary_auth_error_does_not_fall_back() -> None:
    client, fake = make_client([AuthenticationError("401", response=_response(401), body=None)])
    with pytest.raises(AllModelsFailedError):
        client.complete([{"role": "user", "content": "hi"}], [])
    assert len(fake.chat.completions.calls) == 1


def test_all_models_failing_raises_with_every_code() -> None:
    client, fake = make_client(
        [
            RateLimitError("429", response=_response(429), body=None),
            RateLimitError("429", response=_response(429), body=None),
        ],
        max_retries=0,
    )
    with pytest.raises(AllModelsFailedError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}], [])

    assert excinfo.value.codes() == ["rate_limit", "rate_limit"]
    assert len(fake.chat.completions.calls) == 2


def test_no_fallback_configured_raises_a_clear_error() -> None:
    client, fake = make_client(
        [RateLimitError("429", response=_response(429), body=None)],
        fallback=None,
        max_retries=0,
    )
    with pytest.raises(AllModelsFailedError) as excinfo:
        client.complete([{"role": "user", "content": "hi"}], [])

    assert "no fallback is configured" in str(excinfo.value)
    assert len(fake.chat.completions.calls) == 1


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------
def test_tool_calls_are_parsed_with_raw_arguments() -> None:
    client, _ = make_client(
        [
            completion(
                content=None,
                tool_calls=(tool_call("recall_similar_incidents", '{"service":"checkout-api"}'),),
                finish_reason="tool_calls",
            )
        ]
    )
    response = client.complete([{"role": "user", "content": "hi"}], [])

    assert response.has_tool_calls
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].name == "recall_similar_incidents"
    # Arguments stay a raw string: the loop, not the client, validates them.
    assert response.tool_calls[0].arguments == '{"service":"checkout-api"}'
    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def test_a_response_with_no_tool_calls_reports_none() -> None:
    client, _ = make_client([completion("just text")])
    response = client.complete([{"role": "user", "content": "hi"}], [])
    assert not response.has_tool_calls
    assert response.text == "just text"


def test_tools_and_tool_choice_are_forwarded_to_the_provider() -> None:
    client, fake = make_client([completion()])
    specs = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
    client.complete([{"role": "user", "content": "hi"}], specs)

    sent = fake.chat.completions.calls[0]
    assert sent["tools"] == specs
    assert sent["tool_choice"] == "auto"
    assert sent["model"] == PRIMARY


# --------------------------------------------------------------------------
# Model availability
# --------------------------------------------------------------------------
def test_verify_models_passes_when_both_are_available() -> None:
    client, _ = make_client([], models=[PRIMARY, FALLBACK, "other/model"])
    assert client.verify_models() == [PRIMARY, FALLBACK]


def test_verify_models_fails_clearly_when_a_configured_model_is_gone() -> None:
    client, _ = make_client([], models=["some/other-model"])
    with pytest.raises(ModelCallError) as excinfo:
        client.verify_models()

    assert excinfo.value.code is ModelErrorCode.MODEL_NOT_FOUND
    assert PRIMARY in str(excinfo.value)
    assert "some/other-model" in str(excinfo.value)


def test_verify_models_only_checks_the_primary_when_no_fallback_is_set() -> None:
    client, _ = make_client([], models=[PRIMARY], fallback=None)
    assert client.verify_models() == [PRIMARY]


# --------------------------------------------------------------------------
# Config wiring
# --------------------------------------------------------------------------
def test_from_settings_reads_the_configured_models() -> None:
    settings = Settings(
        groq_api_key="secret",  # type: ignore[arg-type]
        groq_model_primary="primary/model",
        groq_model_fallback="fallback/model",
        groq_max_retries=5,
        groq_backoff_base_seconds=0.25,
    )
    fake = FakeGroqClient([])
    client = GroqChatClient.from_settings(settings, client=fake)

    assert client.primary_model == "primary/model"
    assert client.fallback_model == "fallback/model"
    assert client._max_retries == 5
    assert client._backoff_base == 0.25


def test_close_delegates_to_the_provider() -> None:
    client, fake = make_client([])
    client.close()
    assert fake.closed is True


def test_close_swallows_provider_errors() -> None:
    class Broken:
        def close(self) -> None:
            raise RuntimeError("already closed")

    client = GroqChatClient(
        api_key="test", primary_model=PRIMARY, fallback_model=None, client=Broken()
    )
    client.close()  # must not raise
