"""End-to-end tests over the FastAPI ASGI surface."""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def write_client(
    vault_root: Path, tokens: dict[str, str]
) -> AsyncIterator[AsyncClient]:
    os.environ["OBSIDIAN_VAULT_PATH"] = str(vault_root)
    from obsidian_writer.app import app

    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {tokens['write']}"},
        ) as ac:
            yield ac


@pytest.mark.asyncio
async def test_healthz(write_client: AsyncClient, vault_root: Path) -> None:
    r = await write_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["vault"] == str(vault_root.resolve())


@pytest.mark.asyncio
async def test_create_then_read(write_client: AsyncClient) -> None:
    r = await write_client.post(
        "/create",
        json={
            "path": "Inbox/hello.md",
            "title": "Hello",
            "body": "world",
            "tags": ["Test"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["path"] == "Inbox/hello.md"
    assert body["sha"]

    r = await write_client.get("/note", params={"path": "Inbox/hello.md"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Hello"
    # tags are normalised to lowercase server-side
    assert "test" in body["tags"]
    assert "world" in body["body"]


@pytest.mark.asyncio
async def test_create_409_on_duplicate(write_client: AsyncClient) -> None:
    payload = {"path": "Inbox/dup.md", "title": "Dup", "body": "first"}
    r1 = await write_client.post("/create", json=payload)
    assert r1.status_code == 201
    r2 = await write_client.post("/create", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_create_blocks_path_traversal(write_client: AsyncClient) -> None:
    r = await write_client.post(
        "/create", json={"path": "../../etc/passwd", "body": "x"}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_blocks_reserved_top_level(write_client: AsyncClient) -> None:
    r = await write_client.post(
        "/create", json={"path": ".obsidian-map.yaml", "body": "x"}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_append_then_read(write_client: AsyncClient) -> None:
    await write_client.post(
        "/create", json={"path": "Daily/2026-09-21.md", "title": "Today", "body": ""}
    )
    r = await write_client.post(
        "/append",
        json={
            "path": "Daily/2026-09-21.md",
            "heading": "Notes",
            "content": "first entry",
        },
    )
    assert r.status_code == 200
    r = await write_client.get("/note", params={"path": "Daily/2026-09-21.md"})
    assert r.status_code == 200
    assert "first entry" in r.json()["body"]


@pytest.mark.asyncio
async def test_frontmatter_patch_adds_field(write_client: AsyncClient) -> None:
    await write_client.post(
        "/create", json={"path": "Projects/x.md", "title": "X", "body": ""}
    )
    r = await write_client.patch(
        "/frontmatter",
        json={
            "path": "Projects/x.md",
            "patch": {"status": "open", "due": "2026-10-01"},
        },
    )
    assert r.status_code == 200
    r = await write_client.get("/note", params={"path": "Projects/x.md"})
    fm = r.json()["frontmatter"]
    assert fm["status"] == "open"
    assert fm["due"] == "2026-10-01"


@pytest.mark.asyncio
async def test_frontmatter_patch_rejects_reserved(write_client: AsyncClient) -> None:
    await write_client.post(
        "/create", json={"path": "Inbox/y.md", "title": "Y", "body": ""}
    )
    r = await write_client.patch(
        "/frontmatter",
        json={"path": "Inbox/y.md", "patch": {"source": "manual"}},
    )
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_trash_moves_note_and_writes_meta(
    write_client: AsyncClient, vault_root: Path
) -> None:
    await write_client.post(
        "/create", json={"path": "Inbox/old.md", "title": "Old", "body": ""}
    )
    r = await write_client.post(
        "/trash", json={"path": "Inbox/old.md", "reason": "test cleanup"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "trashed"
    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    trashed = vault_root / ".trash" / today / "old.md"
    assert trashed.exists()
    meta = vault_root / ".trash" / today / "old.md.meta.md"
    assert meta.exists()
    assert "original_path: Inbox/old.md" in meta.read_text()


@pytest.mark.asyncio
async def test_search_finds_matches(write_client: AsyncClient) -> None:
    await write_client.post(
        "/create",
        json={"path": "Inbox/alpha.md", "title": "Alpha", "body": "hello world"},
    )
    await write_client.post(
        "/create",
        json={"path": "Inbox/beta.md", "title": "Beta", "body": "goodbye world"},
    )
    r = await write_client.get("/search", params={"q": "world"})
    assert r.status_code == 200
    paths = sorted(m["path"] for m in r.json()["matches"])
    assert paths == ["Inbox/alpha.md", "Inbox/beta.md"]


@pytest.mark.asyncio
async def test_list_folder(write_client: AsyncClient) -> None:
    await write_client.post(
        "/create", json={"path": "Inbox/a.md", "title": "A", "body": ""}
    )
    await write_client.post(
        "/create", json={"path": "Inbox/b.md", "title": "B", "body": ""}
    )
    r = await write_client.get("/list", params={"path": "Inbox"})
    body = r.json()
    assert sorted(body["folders"]) == []
    assert sorted(n["path"] for n in body["notes"]) == ["Inbox/a.md", "Inbox/b.md"]


@pytest.mark.asyncio
async def test_map_endpoints_round_trip(write_client: AsyncClient) -> None:
    r = await write_client.get("/map")
    assert r.status_code == 200
    assert "Inbox" in r.json()["folders"]

    r = await write_client.patch(
        "/map",
        json={
            "patch": {
                "folders": {
                    "Projects": {
                        "kind": "project",
                        "purpose": "Active",
                        "writable": True,
                    }
                }
            }
        },
    )
    assert r.status_code == 200

    r = await write_client.get("/map")
    assert "Projects" in r.json()["folders"]
    assert "Inbox" in r.json()["folders"]


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_minute_quota(write_client: AsyncClient) -> None:
    """5/min limit, 6 sequential creates => last must 429."""
    for i in range(5):
        r = await write_client.post(
            "/create", json={"path": f"Inbox/r{i}.md", "title": f"R{i}", "body": ""}
        )
        assert r.status_code == 201, (i, r.text)
    r = await write_client.post(
        "/create", json={"path": "Inbox/r6.md", "title": "R6", "body": ""}
    )
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_read_token_cannot_write(
    vault_root: Path, tokens: dict[str, str]
) -> None:
    os.environ["OBSIDIAN_VAULT_PATH"] = str(vault_root)
    from obsidian_writer.app import app

    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {tokens['read']}"},
        ) as ac:
            r = await ac.post(
                "/create", json={"path": "Inbox/x.md", "title": "X", "body": ""}
            )
            assert r.status_code == 401


@pytest.mark.asyncio
async def test_write_token_can_read(write_client: AsyncClient) -> None:
    r = await write_client.get("/note", params={"path": "Inbox/missing.md"})
    assert r.status_code == 404
