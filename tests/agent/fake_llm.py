"""Scripted stand-ins for the Groq client. No network, no LLM, no flakiness."""

from __future__ import annotations

import json
from typing import Any

from src.agent.groq_client import ModelResponse, ToolCallRequest
from src.agent.trace import ModelCall, ModelRole
from src.memory.trace import utcnow

PRIMARY = "openai/gpt-oss-120b"
FALLBACK = "qwen/qwen3.8-27b"


def call(name: str, arguments: dict[str, Any] | str, call_id: str = "call_1") -> ToolCallRequest:
    """A tool call with raw JSON arguments, exactly as a model would emit it."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCallRequest(id=call_id, name=name, arguments=raw)


def tool_response(
    *calls: ToolCallRequest,
    model: str = PRIMARY,
    role: ModelRole = ModelRole.PRIMARY,
    text: str | None = None,
) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=list(calls),
        model=model,
        role=role,
        finish_reason="tool_calls",
    )


def tc(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> ModelResponse:
    """Shorthand for a response containing exactly one tool call."""
    return tool_response(call(name, arguments, call_id))


def text_response(
    text: str = "Proposed a diagnosis; awaiting operator confirmation.",
    model: str = PRIMARY,
    role: ModelRole = ModelRole.PRIMARY,
) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=[], model=model, role=role, finish_reason="stop")


class FakeLLM:
    """Pops scripted responses in order and records every message list it saw."""

    def __init__(self, script: list[ModelResponse | BaseException]) -> None:
        self.script = list(script)
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.seen_tools: list[Any] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        record: Any = None,
    ) -> ModelResponse:
        self.seen_messages.append([dict(message) for message in messages])
        self.seen_tools.append(tools)

        if not self.script:
            raise AssertionError(
                "FakeLLM script exhausted: the loop made an unexpected extra model call"
            )

        item = self.script.pop(0)
        if isinstance(item, BaseException):
            if record is not None:
                record(
                    ModelCall(
                        model=PRIMARY,
                        role=ModelRole.PRIMARY,
                        attempt=1,
                        started_at=utcnow(),
                        ok=False,
                        error_code="unknown",
                        error_message=str(item),
                    )
                )
            raise item

        if record is not None:
            record(
                ModelCall(
                    model=item.model,
                    role=item.role,
                    attempt=1,
                    started_at=utcnow(),
                    ok=True,
                    finish_reason=item.finish_reason,
                    tool_call_count=len(item.tool_calls),
                )
            )
        return item

    @property
    def calls_made(self) -> int:
        return len(self.seen_messages)

    def messages_of(self, index: int) -> list[dict[str, Any]]:
        return self.seen_messages[index]

    def all_message_text(self) -> str:
        return json.dumps(self.seen_messages)
