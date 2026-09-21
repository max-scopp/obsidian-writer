"""FastAPI application for obsidian-writer.

The application is a thin shell: configuration is loaded from the
environment on startup, the vault is mounted and validated once, and
every endpoint delegates to a focused helper in `endpoints.py`.

Auth and rate limiting live in `deps.py` so the dependency functions can
be imported by both this module and `endpoints.py` without creating a
circular import.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .endpoints import router as endpoints_router
from .io import cleanup_stale_tmp
from .ratelimit import Limits, RateLimiter

log = logging.getLogger("obsidian_writer")


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"environment variable {name} is required")
    return value or ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    vault_path = Path(_env("OBSIDIAN_VAULT_PATH", "/vault"))
    if not vault_path.is_dir():
        raise RuntimeError(f"vault path does not exist or is not a directory: {vault_path}")

    write_token = _env("OBSIDIAN_WRITER_TOKEN", required=True)
    read_token = _env("OBSIDIAN_WRITER_READ_TOKEN", write_token)

    limits = Limits(
        per_minute=int(_env("OBSIDIAN_RATE_PER_MIN", "30")),
        per_day=int(_env("OBSIDIAN_RATE_PER_DAY", "200")),
    )

    # Sweep stale tmp files left over from a crash, max age 24h.
    removed = cleanup_stale_tmp(vault_path, max_age_seconds=86400)
    if removed:
        log.warning("cleaned %d stale tmp files on startup", removed)

    log.info(
        "obsidian-writer %s starting (vault=%s, burst=%d/min, day=%d/day)",
        __version__,
        vault_path,
        limits.per_minute,
        limits.per_day,
    )

    app.state.vault_root = vault_path.resolve(strict=False)
    app.state.write_token = write_token
    app.state.read_token = read_token
    app.state.limiter = RateLimiter(limits)

    try:
        yield
    finally:
        log.info("obsidian-writer shutting down")


app = FastAPI(
    title="obsidian-writer",
    version=__version__,
    summary="AI-agent-facing vault read/write service for Obsidian",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    return {
        "status": "ok",
        "vault": str(request.app.state.vault_root),
        "version": __version__,
    }


app.include_router(endpoints_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Catch-all so a single broken endpoint doesn't leak internals."""
    log.exception("unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "internal server error"},
    )
