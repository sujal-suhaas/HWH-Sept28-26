"""The agent loop.

Bounded and defensive by construction:

* at most ``max_steps`` model turns,
* every tool call validated before execution,
* a malformed call gets exactly one repair, then the run ends honestly,
* a model that fails retryably falls back once, and a total failure ends the run
  with an error rather than a fabricated answer,
* the loop stops as soon as the model replies without tool calls.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from src.agent.groq_client import AllModelsFailedError, GroqChatClient, ModelResponse
from src.agent.prompts import (
    SYSTEM_PROMPT,
    render_alert,
    render_memory_off_notice,
    render_tool_validation_error,
)
from src.agent.tools import (
    TOOL_SPECS,
    ToolContext,
    execute_tool_call,
    validate_grounding,
    validate_tool_call,
)
from src.agent.trace import AgentRun, AgentStatus, ModelCall, ModelRole, ToolCall
from src.catalog import Catalog
from src.contracts import AlertPayload
from src.memory import MemoryStore
from src.memory.trace import MemoryTrace

logger = logging.getLogger(__name__)

MAX_REPAIRS_PER_TOOL = 1


#: Callback used to register a memory trace with whatever owns the trace log.
TraceSink = Callable[[MemoryTrace], None]


def _assistant_message(response: ModelResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.text or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ],
    }


def _tool_message(call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class AgentLoop:
    def __init__(
        self,
        *,
        memory: MemoryStore,
        llm: GroqChatClient,
        catalog: Catalog,
        max_steps: int = 6,
        on_memory_trace: TraceSink | None = None,
    ) -> None:
        self.memory = memory
        self.llm = llm
        self.catalog = catalog
        self.max_steps = max(1, max_steps)
        self.on_memory_trace = on_memory_trace

    # --- message assembly ---------------------------------------------------
    def _initial_messages(
        self, alert: AlertPayload, memory_mode: str, extra_user_messages: list[str] | None = None
    ) -> list[dict[str, Any]]:
        system = SYSTEM_PROMPT
        if memory_mode == "off":
            system = f"{system}\n\n{render_memory_off_notice()}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": render_alert(alert)},
        ]
        # Follow-up turns, e.g. an operator chat question about the same incident.
        for text in extra_user_messages or []:
            messages.append({"role": "user", "content": text})
        return messages

    # --- one tool call ------------------------------------------------------
    def _run_tool_call(
        self,
        *,
        call_id: str,
        name: str,
        raw_arguments: str,
        ctx: ToolContext,
        repairs: dict[str, int],
        run: AgentRun,
    ) -> tuple[ToolCall, dict[str, Any]]:
        """Validate, and if valid execute, a single tool call.

        Returns the trace record and the message to feed back to the model.
        """
        started = time.perf_counter()
        record = ToolCall(call_id=call_id, name=name, raw_arguments=raw_arguments)

        args, errors = validate_tool_call(name, raw_arguments)
        if args is not None:
            errors = validate_grounding(name, args, ctx)

        record.latency_ms = round((time.perf_counter() - started) * 1000, 2)

        if errors:
            record.validation_errors = errors
            already_repaired = repairs.get(name, 0)
            if already_repaired >= MAX_REPAIRS_PER_TOOL:
                record.repair_attempted = False
                logger.warning("tool call %s still invalid after repair: %s", name, errors)
                return record, _tool_message(
                    call_id,
                    "Rejected again; giving up. " + "; ".join(errors),
                )
            repairs[name] = already_repaired + 1
            record.repair_attempted = True
            return record, _tool_message(call_id, render_tool_validation_error(name, errors))

        assert args is not None
        record.valid = True
        record.arguments = args.model_dump(mode="json")

        started_exec = time.perf_counter()
        outcome = execute_tool_call(name, args, ctx)
        record.executed = True
        record.result_summary = outcome.summary
        record.result_data = outcome.data
        record.latency_ms = round((time.perf_counter() - started_exec) * 1000, 2)
        return record, _tool_message(call_id, outcome.summary)

    # --- main loop ----------------------------------------------------------
    def run(
        self,
        alert: AlertPayload,
        incident_id: str,
        *,
        extra_user_messages: list[str] | None = None,
    ) -> AgentRun:
        memory_mode = str(getattr(self.memory, "mode", "on"))
        ctx = ToolContext(
            incident_id=incident_id,
            memory=self.memory,
            catalog=self.catalog,
            memory_mode=memory_mode,
            on_memory_trace=self.on_memory_trace,
        )
        run = AgentRun(incident_id=incident_id, memory_mode=memory_mode)
        messages = self._initial_messages(alert, memory_mode, extra_user_messages)
        repairs: dict[str, int] = {}
        exhausted = True

        for step in range(self.max_steps):
            run.steps = step + 1

            try:
                response = self.llm.complete(
                    messages,
                    TOOL_SPECS,
                    record=run.model_calls.append,
                )
            except AllModelsFailedError as exc:
                logger.error("agent run %s: %s", run.run_id, exc)
                return self._finalize(
                    run, ctx, status=AgentStatus.MODEL_FAILED, error=str(exc)
                )

            run.model_used = response.model
            run.used_fallback = response.role is ModelRole.FALLBACK

            if not response.has_tool_calls:
                run.final_text = response.text
                exhausted = False
                break

            messages.append(_assistant_message(response))

            for call in response.tool_calls:
                record, message = self._run_tool_call(
                    call_id=call.id,
                    name=call.name,
                    raw_arguments=call.arguments,
                    ctx=ctx,
                    repairs=repairs,
                    run=run,
                )
                run.tool_calls.append(record)
                messages.append(message)

                if record.validation_errors and not record.repair_attempted:
                    detail = f"tool call '{call.name}' invalid after repair: " + "; ".join(
                        record.validation_errors
                    )
                    return self._finalize(
                        run, ctx, status=AgentStatus.TOOL_CALL_INVALID, error=detail
                    )

        if exhausted:
            return self._finalize(
                run,
                ctx,
                status=AgentStatus.MAX_STEPS,
                error=f"model did not stop within {self.max_steps} steps",
            )
        return self._finalize(run, ctx, status=AgentStatus.COMPLETED)

    @staticmethod
    def _finalize(
        run: AgentRun,
        ctx: ToolContext,
        *,
        status: AgentStatus,
        error: str | None = None,
    ) -> AgentRun:
        run.diagnosis = ctx.diagnosis.model_dump(mode="json") if ctx.diagnosis else None
        run.resolution = ctx.resolution.model_dump(mode="json") if ctx.resolution else None
        run.recalled_memory_ids = sorted(ctx.recalled_ids)
        run.memory_trace_ids = list(ctx.memory_trace_ids)
        return run.finish(status=status, error=error)

    @staticmethod
    def model_call_summary(calls: list[ModelCall]) -> dict[str, Any]:
        """Compact counts for logging and the API response."""
        return {
            "total": len(calls),
            "failed": sum(1 for call in calls if not call.ok),
            "fallback": sum(1 for call in calls if call.role is ModelRole.FALLBACK),
        }
