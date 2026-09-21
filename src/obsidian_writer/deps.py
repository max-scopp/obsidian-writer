"""Shared FastAPI dependencies.

Lives in its own module to avoid circular imports between `app.py` (which
defines the FastAPI instance) and `endpoints.py` (which uses these
dependencies).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Header, HTTPException, Request, status

log = logging.getLogger("obsidian_writer.deps")


def get_vault(request: Request) -> Path:
    return request.app.state.vault_root


def require_write_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    token = _extract_token(authorization)
    if token != request.app.state.write_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return token


def require_read_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    token = _extract_token(authorization)
    if token not in (request.app.state.write_token, request.app.state.read_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return token


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
