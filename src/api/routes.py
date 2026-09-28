"""Route definitions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from src import __version__
from src.config import Settings

router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/health", tags=["system"])
def health(request: Request) -> dict[str, Any]:
    """Cheap liveness check. Makes no external calls."""
    settings = _settings(request)
    return {
        "status": "ok",
        "version": __version__,
        "memory_mode": settings.memory_mode,
        "bank_id": settings.hindsight_bank_id,
        "model_primary": settings.groq_model_primary,
        "model_fallback": settings.groq_model_fallback,
    }


@router.get("/health/memory", tags=["system"])
def memory_health(request: Request) -> dict[str, Any]:
    """Deep check: does the memory layer actually answer?"""
    store = request.app.state.memory
    trace = request.app.state.traces.add(store.health())
    return trace.model_dump(mode="json")


@router.get("/api/memory/traces", tags=["memory"])
def memory_traces(request: Request, limit: int = 50) -> dict[str, Any]:
    """Recent memory traces, newest last. Backs the Memory Inspector."""
    traces = request.app.state.traces.all()[-limit:]
    return {"count": len(traces), "traces": [t.model_dump(mode="json") for t in traces]}
