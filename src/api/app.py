"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import __version__
from src.agent.groq_client import GroqChatClient
from src.api.routes import router
from src.api.sqlite_store import SqliteIncidentStore
from src.catalog import get_catalog
from src.config import Settings, get_settings
from src.logging_setup import configure_logging
from src.memory import DisabledMemoryStore, TraceLog, build_memory_store

logger = logging.getLogger(__name__)


def build_llm(settings: Settings) -> GroqChatClient | None:
    """The Groq client, or ``None`` when no key is configured.

    A missing key is not a crash: health, incident reads, and the UI all work
    without it. Only the routes that need a model return 503, and they say why.
    """
    if not settings.groq_api_key.get_secret_value():
        logger.warning("GROQ_API_KEY is not set: POST /alerts and POST /chat will return 503")
        return None
    return GroqChatClient.from_settings(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = app.state.memory
    # The memory adapter marshals every Hindsight call onto its own event loop,
    # so it is safe to call from here and from FastAPI's handler threadpool.
    trace = store.create_bank_if_needed()
    if trace.success:
        logger.info("memory ready (bank=%s, mode=%s)", store.bank_id, store.mode)
    else:
        logger.warning(
            "memory unavailable at startup (code=%s): %s - continuing in degraded mode",
            trace.error_code.value,
            trace.error_message,
        )
    app.state.traces.add(trace)

    try:
        yield
    finally:
        store.close()
        if app.state.llm is not None:
            app.state.llm.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    app = FastAPI(
        title="DejaOps",
        version=__version__,
        description="On-call incident response agent with cumulative Hindsight memory.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.traces = TraceLog()
    app.state.memory = build_memory_store(settings)
    # The memory-off store, for per-request overrides. Kept separate so one alert
    # can be run both ways without a restart, which the §4 comparison requires.
    app.state.memory_off = DisabledMemoryStore(bank_id=settings.hindsight_bank_id)
    app.state.catalog = get_catalog()
    app.state.incidents = SqliteIncidentStore(settings.sqlite_path)
    app.state.llm = build_llm(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
