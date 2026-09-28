"""Agent core: Groq client, tool definitions, and the bounded agent loop."""

from __future__ import annotations

from src.agent.groq_client import (
    AllModelsFailedError,
    GroqChatClient,
    ModelCallError,
    ModelErrorCode,
    ModelResponse,
    ToolCallRequest,
    classify_model_error,
)
from src.agent.loop import AgentLoop
from src.agent.tools import (
    ALLOWED_TOOLS,
    PROPOSAL_TOOLS,
    READ_TOOLS,
    TOOL_ARG_MODELS,
    TOOL_SPECS,
    ToolContext,
    ToolOutcome,
    validate_grounding,
    validate_tool_call,
)
from src.agent.trace import AgentRun, AgentStatus, ModelCall, ModelRole, ToolCall

__all__ = [
    "ALLOWED_TOOLS",
    "PROPOSAL_TOOLS",
    "READ_TOOLS",
    "TOOL_ARG_MODELS",
    "TOOL_SPECS",
    "AgentLoop",
    "AgentRun",
    "AgentStatus",
    "AllModelsFailedError",
    "GroqChatClient",
    "ModelCall",
    "ModelCallError",
    "ModelErrorCode",
    "ModelResponse",
    "ModelRole",
    "ToolCall",
    "ToolCallRequest",
    "ToolContext",
    "ToolOutcome",
    "classify_model_error",
    "validate_grounding",
    "validate_tool_call",
]
