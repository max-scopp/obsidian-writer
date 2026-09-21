"""Pytest fixtures.

The tests use a tmp-path vault root and run against the FastAPI app via
the `httpx.AsyncClient` + `ASGITransport` pattern with `asgi-lifespan`
to fire the FastAPI lifespan (so app.state is populated). This avoids
needing a real network listener and lets us swap the vault root per-test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    write = "test-write-token-1234567890"
    read = "test-read-token-0987654321"
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "")
    monkeypatch.setenv("OBSIDIAN_WRITER_TOKEN", write)
    monkeypatch.setenv("OBSIDIAN_WRITER_READ_TOKEN", read)
    monkeypatch.setenv("OBSIDIAN_RATE_PER_MIN", "5")
    monkeypatch.setenv("OBSIDIAN_RATE_PER_DAY", "20")
    return {"write": write, "read": read}


async def _client_with_lifespan(
    vault_root: Path, auth_header: str
) -> AsyncIterator[AsyncClient]:
    os.environ["OBSIDIAN_VAULT_PATH"] = str(vault_root)

    from obsidian_writer.app import app  # noqa: PLC0415

    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": auth_header},
        ) as ac:
            yield ac


@pytest_asyncio.fixture
async def client(
    vault_root: Path, tokens: dict[str, str]
) -> AsyncIterator[AsyncClient]:
    async for c in _client_with_lifespan(vault_root, f"Bearer {tokens['write']}"):
        yield c


@pytest_asyncio.fixture
async def read_client(
    vault_root: Path, tokens: dict[str, str]
) -> AsyncIterator[AsyncClient]:
    async for c in _client_with_lifespan(vault_root, f"Bearer {tokens['read']}"):
        yield c
