"""Deterministic backend for the Playwright happy path.

Real routes, real contract, real trace log — only the model and the memory
provider are replaced with fakes, so the run is reproducible and needs no API
keys. This is a test harness, not the application.

    uv run python scripts/serve_e2e.py --port 8000

The live path (real Hindsight, real Groq) is exercised by the demo and by
``scripts/seed_memory.py``; see docs/architecture.md.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.app import create_app  # noqa: E402
from src.config import Settings  # noqa: E402
from src.memory import InMemoryMemoryStore  # noqa: E402
from tests.agent.fake_llm import FakeLLM, call, text_response, tool_response  # noqa: E402
from tests.api.helpers import CHECKOUT, seed_confirmed_resolution  # noqa: E402

#: `_render_hits` writes each recalled memory as `- [<memory id>] ...`.
_MEMORY_ID = re.compile(r"- \[([^\]]+)\]")
#: The same line annotates the runbook the memory came from, if any.
_RUNBOOK_ID = re.compile(r"runbook_id=(RB-\d+)")


class MemoryAwareLLM(FakeLLM):
    """A model that cites only what it was actually shown.

    A fixed script cannot serve both memory modes. The agent's grounding guards
    reject a citation the model was never given, so the same proposal must cite a
    recalled memory when memory is on and cite nothing at all when it is off.
    Reading the tool results in the conversation and citing what is there is what
    a real model does — and it means this harness exercises the guards instead of
    sidestepping them.
    """

    def __init__(self) -> None:
        super().__init__([])

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, record: Any = None
    ) -> Any:
        self.script = [self._next(messages)]
        return super().complete(messages, tools, record=record)

    @staticmethod
    def _next(messages: list[dict[str, Any]]) -> Any:
        results = [
            str(item.get("content") or "") for item in messages if item.get("role") == "tool"
        ]
        seen = "\n".join(results)
        cited = _MEMORY_ID.findall(seen)
        runbooks = _RUNBOOK_ID.findall(seen)
        step = len(results)

        if step == 0:
            return tool_response(
                call(
                    "recall_similar_incidents",
                    {"service": CHECKOUT, "symptom": "p99 latency after payments-ledger deploy"},
                    call_id="call_1",
                )
            )
        if step == 1:
            return tool_response(
                call(
                    "propose_diagnosis",
                    {
                        "hypothesis": "Kafka consumer lag on payments-ledger after the deploy.",
                        "confidence": "high",
                        "evidence_summary": (
                            "Latency spiked right after the ledger deploy."
                            if cited
                            else "No prior incident matched, so this is ungrounded."
                        ),
                        "cited_memory_ids": cited,
                    },
                    call_id="call_2",
                )
            )
        if step == 2:
            return tool_response(
                call(
                    "propose_resolution",
                    {
                        "fix": "Scale the ledger consumer group and replay the affected partition.",
                        "evidence_summary": (
                            "Matches a validated fix for this signature."
                            if cited
                            else "No validated fix was found for this signature."
                        ),
                        "cited_memory_ids": cited,
                        "runbook_id": runbooks[0] if runbooks else None,
                    },
                    call_id="call_3",
                )
            )
        return text_response("Diagnosis and fix proposed; awaiting operator confirmation.")


def build_app() -> object:
    settings = Settings(
        memory_mode="on",
        hindsight_api_key="e2e-key",
        hindsight_bank_id="dejaops-e2e",
        groq_api_key="e2e-key",
        # A real file, not ":memory:": the store opens a connection per operation,
        # so an in-memory database would be empty on every call.
        sqlite_path=str(Path(tempfile.mkdtemp()) / "dejaops-e2e.db"),
        # The browser is served from 127.0.0.1, which is not the default origin.
        cors_origins="http://127.0.0.1:5173,http://localhost:5173",
    )
    app = create_app(settings)

    store = InMemoryMemoryStore(bank_id=settings.hindsight_bank_id)
    seed_confirmed_resolution(store)
    app.state.memory = store
    app.state.llm = MemoryAwareLLM()
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
