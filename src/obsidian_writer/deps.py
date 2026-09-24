"""Shared FastAPI dependencies.

Lives in its own module to avoid circular imports between `app.py` (which
defines the FastAPI instance) and `endpoints.py` (which uses these
dependencies).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, Header, HTTPException, Request, status

log = logging.getLogger("obsidian_writer.deps")


def get_vault(request: Request) -> Path:
    return request.app.state.vault_root


def require_write_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    token = _extract_token(authorization)
    if token not in request.app.state.writers:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return token


def require_read_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    token = _extract_token(authorization)
    if token not in request.app.state.writers and token != request.app.state.read_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return token


def actor(
    request: Request,
    token: str = Depends(require_write_token),
    x_obsidian_actor: str | None = Header(default=None),
) -> str:
    """Who is writing, for the history: the client, plus whom it acts for.

    Each write token belongs to a named client. A client that fronts several
    callers - the LiteLLM plugin serves LobeHub, Home Assistant and n8n on
    one token - names the caller in `X-Obsidian-Actor`, giving
    `litellm/lobehub`. The header is attribution, not authentication: only
    a holder of a write token can send it.
    """
    client: str = request.app.state.writers[token]
    caller = (x_obsidian_actor or "").strip()
    return f"{client}/{caller}" if caller else client


def rate_limited(token: str, request: Request) -> None:
    allowed, reason = request.app.state.limiter.check(token)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=reason or "rate limit exceeded",
        )


def _extract_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()
