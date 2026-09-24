"""Canvas (*.canvas) reads and writes over the ASGI surface."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from obsidian_writer.canvas import CanvasError, dumps, loads


def node(nid: str, text: str = "hi", x: int = 0, y: int = 0) -> dict:
    return {
        "id": nid, "type": "text", "x": x, "y": y,
        "width": 300, "height": 120, "text": text,
    }


@pytest_asyncio.fixture
async def client(vault_root: Path, tokens: dict[str, str]) -> AsyncIterator[AsyncClient]:
    os.environ["OBSIDIAN_VAULT_PATH"] = str(vault_root)
    from obsidian_writer.app import app

    async with LifespanManager(app) as manager, AsyncClient(
        transport=ASGITransport(app=manager.app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {tokens['write']}"},
    ) as ac:
        yield ac


# ---- pure validation ------------------------------------------------------


def test_loads_empty_file_is_empty_canvas() -> None:
    assert loads("") == {"nodes": [], "edges": []}


def test_dumps_puts_nodes_and_edges_first_and_keeps_extras() -> None:
    out = dumps({"zoom": 1, "edges": [], "nodes": []})
    assert list(json.loads(out)) == ["nodes", "edges", "zoom"]
    assert out.endswith("\n")


@pytest.mark.parametrize(
    "doc,fragment",
    [
        (
            {"nodes": [{"id": "a", "type": "text", "x": 0, "y": 0, "width": 1, "height": 1}]},
            "needs a string",
        ),
        ({"nodes": [node("a"), node("a")]}, "duplicated"),
        ({"nodes": [], "edges": [{"id": "e", "fromNode": "a", "toNode": "b"}]}, "unknown node"),
        (
            {"nodes": [{"id": "a", "type": "nope", "x": 0, "y": 0, "width": 1, "height": 1}]},
            "type must be one of",
        ),
        (
            {
                "nodes": [
                    {
                        "id": "a",
                        "type": "text",
                        "x": "0",
                        "y": 0,
                        "width": 1,
                        "height": 1,
                        "text": "t",
                    }
                ]
            },
            "must be a number",
        ),
        ({"nodes": "no"}, "must be an array"),
    ],
)
def test_validate_rejects(doc: dict, fragment: str) -> None:
    with pytest.raises(CanvasError, match=fragment):
        loads(json.dumps(doc))


# ---- endpoints ------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_then_get_roundtrip(client: AsyncClient, vault_root: Path) -> None:
    body = {
        "path": "Tech/Architecture",  # extension appended by the service
        "nodes": [node("a", "Gateway"), node("b", "Worker", x=400)],
        "edges": [{"id": "e1", "fromNode": "a", "toNode": "b", "toEnd": "arrow"}],
    }
    r = await client.put("/canvas", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "created"
    assert r.json()["path"] == "Tech/Architecture.canvas"

    on_disk = json.loads((vault_root / "Tech/Architecture.canvas").read_text())
    assert [n["id"] for n in on_disk["nodes"]] == ["a", "b"]

    r = await client.get("/canvas", params={"path": "Tech/Architecture.canvas"})
    assert r.status_code == 200
    assert len(r.json()["nodes"]) == 2
    assert r.json()["edges"][0]["toEnd"] == "arrow"


@pytest.mark.asyncio
async def test_put_twice_replaces(client: AsyncClient) -> None:
    await client.put("/canvas", json={"path": "c", "nodes": [node("a")], "edges": []})
    r = await client.put("/canvas", json={"path": "c", "nodes": [], "edges": []})
    assert r.json()["kind"] == "updated"
    assert r.json()["detail"] == "0 nodes, 0 edges"


@pytest.mark.asyncio
async def test_put_rejects_dangling_edge(client: AsyncClient) -> None:
    r = await client.put(
        "/canvas",
        json={
            "path": "c",
            "nodes": [node("a")],
            "edges": [{"id": "e", "fromNode": "a", "toNode": "ghost"}],
        },
    )
    assert r.status_code == 422
    assert "unknown node" in r.text


@pytest.mark.asyncio
async def test_get_missing_canvas_is_404(client: AsyncClient) -> None:
    r = await client.get("/canvas", params={"path": "nope.canvas"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_malformed_canvas_is_422(client: AsyncClient, vault_root: Path) -> None:
    (vault_root / "broken.canvas").write_text("{not json")
    r = await client.get("/canvas", params={"path": "broken.canvas"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_refuses_canvas_paths(client: AsyncClient) -> None:
    r = await client.post("/create", json={"path": "Tech/x.canvas", "body": "y"})
    assert r.status_code == 400
    assert "PUT /canvas" in r.text


@pytest.mark.asyncio
async def test_canvas_path_traversal_blocked(client: AsyncClient) -> None:
    r = await client.put("/canvas", json={"path": "../escape", "nodes": [], "edges": []})
    assert r.status_code in (400, 403)
