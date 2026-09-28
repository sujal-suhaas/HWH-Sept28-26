"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import __version__
from src.api.routes import router
from src.config import Settings, get_settings
from src.logging_setup import configure_logging
from src.memory import TraceLog, build_memory_store

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = app.state.memory
    # The Hindsight SDK is synchronous and cannot run inside a live event loop,
    # so the startup probe is offloaded to a worker thread. All request
    # handlers are sync `def`, so FastAPI runs them in the threadpool too and
    # the whole memory layer stays synchronous.
    trace = await asyncio.to_thread(store.create_bank_if_needed)
    if trace.success:
        logger.info("memory ready (bank=%s, mode=%s)", store.bank_id, trace.mode.value)
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
