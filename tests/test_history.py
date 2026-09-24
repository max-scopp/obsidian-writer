"""End-to-end tests for vault history, in-place edits and client attribution."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from obsidian_writer.history import History, author_name

BRIDGE_TOKEN = "bridge-token-abcdefghijklmnop"

CORE = """---
type: core
scope: shared
topics: [preferences]
updated: 2026-09-22
---

# Preferences

## Development

- TypeScript.
"""


@pytest.fixture
def history_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Outside the vault on purpose: that is how the service is deployed.
    return tmp_path_factory.mktemp("history") / "vault.git"


@pytest_asyncio.fixture
async def app_client(
    vault_root: Path,
    tokens: dict[str, str],
    history_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    (vault_root / "Core").mkdir()
    (vault_root / "Core" / "Preferences.md").write_text(CORE, encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_HISTORY_DIR", str(history_dir))
    monkeypatch.setenv("OBSIDIAN_WRITER_TOKEN_NAME", "litellm")
    monkeypatch.setenv("OBSIDIAN_WRITER_CLIENTS", f"memory-bridge={BRIDGE_TOKEN}")
    monkeypatch.setenv("OBSIDIAN_RATE_PER_MIN", "100")
    monkeypatch.setenv("OBSIDIAN_RATE_PER_DAY", "1000")
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


async def _log(client: AsyncClient, path: str | None = None) -> list[dict[str, str]]:
    params = {"path": path} if path else {}
    r = await client.get("/history", params=params)
    assert r.status_code == 200, r.text
    entries: list[dict[str, str]] = r.json()["entries"]
    return entries


@pytest.mark.asyncio
async def test_startup_records_the_vault_as_it_is(app_client: AsyncClient) -> None:
    entries = await _log(app_client)
    assert [(e["author"], e["message"]) for e in entries] == [
        ("obsidian", "Initial import of the vault")
    ]


@pytest.mark.asyncio
async def test_section_edit_is_committed_and_attributed(app_client: AsyncClient) -> None:
    r = await app_client.put(
        "/section",
        headers={"X-Obsidian-Actor": "lobehub"},
        json={"path": "Core/Preferences.md", "heading": "Development", "content": "- Rust."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["detail"] == "section replaced"

    latest = (await _log(app_client, "Core/Preferences.md"))[0]
    assert latest["author"] == "litellm/lobehub"
    assert latest["message"] == "Rewrite section 'Development' in Core/Preferences.md"

    note = (await app_client.get("/note", params={"path": "Core/Preferences.md"})).json()
    assert "- Rust." in note["body"] and "TypeScript" not in note["body"]
    assert str(note["frontmatter"]["updated"]) != "2026-09-22"


@pytest.mark.asyncio
async def test_hand_edit_is_credited_to_obsidian_not_to_the_next_writer(
    app_client: AsyncClient, vault_root: Path
) -> None:
    note = vault_root / "Core" / "Preferences.md"
    note.write_text(CORE.replace("- TypeScript.", "- TypeScript.\n- Go."), encoding="utf-8")

    r = await app_client.post(
        "/edit",
        json={"path": "Core/Preferences.md", "old": "- TypeScript.", "new": "- TS."},
    )
    assert r.status_code == 200, r.text

    authors = [e["author"] for e in await _log(app_client, "Core/Preferences.md")]
    assert authors[:2] == ["litellm", "obsidian"]


@pytest.mark.asyncio
async def test_named_client_gets_its_own_author(app_client: AsyncClient) -> None:
    await app_client.post("/create", json={"path": "Inbox/Memory Review.md", "body": "x"})
    r = await app_client.put(
        "/section",
        headers={"Authorization": f"Bearer {BRIDGE_TOKEN}"},
        json={"path": "Inbox/Memory Review.md", "heading": "Pending", "content": "- [ ] a"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["detail"] == "section added"
    latest = (await _log(app_client, "Inbox/Memory Review.md"))[0]
    assert latest["author"] == "memory-bridge"


@pytest.mark.asyncio
async def test_edit_refuses_a_stale_read(app_client: AsyncClient) -> None:
    r = await app_client.post(
        "/edit",
        json={
            "path": "Core/Preferences.md",
            "old": "TypeScript",
            "new": "TS",
            "expect_sha": "0" * 64,
        },
    )
    assert r.status_code == 409
    assert "changed since" in r.json()["detail"]


@pytest.mark.asyncio
async def test_edit_refuses_a_passage_that_is_not_there(app_client: AsyncClient) -> None:
    r = await app_client.post(
        "/edit", json={"path": "Core/Preferences.md", "old": "Haskell", "new": "x"}
    )
    assert r.status_code == 409
    assert "read it again" in r.json()["detail"]


@pytest.mark.asyncio
async def test_section_404_when_creation_is_off(app_client: AsyncClient) -> None:
    r = await app_client.put(
        "/section",
        json={"path": "Core/Preferences.md", "heading": "Food", "content": "x", "create": False},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_snapshot_records_hand_edits(app_client: AsyncClient, vault_root: Path) -> None:
    (vault_root / "Inbox").mkdir(exist_ok=True)
    (vault_root / "Inbox" / "idea.md").write_text("# Idea\n", encoding="utf-8")

    r = await app_client.post("/snapshot")
    assert r.status_code == 200
    assert r.json()["sha"]
    latest = (await _log(app_client))[0]
    assert (latest["author"], latest["message"]) == ("obsidian", "Edit in Obsidian")

    again = await app_client.post("/snapshot")
    assert again.json() == {"kind": "recorded", "path": "", "sha": None, "detail": "no changes"}


@pytest.mark.asyncio
async def test_trash_is_recorded_as_a_deletion(app_client: AsyncClient) -> None:
    r = await app_client.post("/trash", json={"path": "Core/Preferences.md", "reason": "test"})
    assert r.status_code == 201, r.text
    latest = (await _log(app_client))[0]
    assert latest["message"] == "Trash Core/Preferences.md: test"


@pytest.mark.asyncio
async def test_create_in_the_vault_contract(app_client: AsyncClient, vault_root: Path) -> None:
    r = await app_client.post(
        "/create",
        json={
            "path": "Knowledge/Home/Heat Pump",
            "title": "Heat Pump",
            "body": "Runs on a 55 C flow.",
            "frontmatter": {
                "type": "knowledge",
                "topics": ["home", "heating"],
                "source": "inferred",
            },
        },
    )
    assert r.status_code == 201, r.text
    text = (vault_root / "Knowledge" / "Home" / "Heat Pump.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntype: knowledge\nscope: shared\ntopics: [home, heating]\n")
    assert "source: inferred\n---\n\n# Heat Pump\n\nRuns on a 55 C flow.\n" in text
    assert "source: chat" not in text and "created:" not in text


@pytest.mark.asyncio
async def test_source_can_be_cleared_once_confirmed(app_client: AsyncClient) -> None:
    await app_client.post(
        "/create",
        json={
            "path": "Memory/Events/Move",
            "frontmatter": {"type": "memory", "source": "inferred"},
        },
    )
    r = await app_client.patch(
        "/frontmatter", json={"path": "Memory/Events/Move.md", "patch": {"source": None}}
    )
    assert r.status_code == 200, r.text
    fm = (await app_client.get("/note", params={"path": "Memory/Events/Move.md"})).json()[
        "frontmatter"
    ]
    assert "source" not in fm
    assert "modified" not in fm  # a vault note records edits in `updated` only


def test_author_name_is_sanitised() -> None:
    assert author_name("litellm/Home Assistant!") == "litellm/Home-Assistant"
    assert author_name("") == "unknown"


def test_history_skips_paths_that_do_not_exist_yet(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    history = History(tmp_path / "vault.git", vault)
    history.ensure()
    assert history.checkpoint(["Inbox/new.md"]) is None


@pytest.mark.asyncio
async def test_edit_whole_line_by_fragment(app_client: AsyncClient, vault_root: Path) -> None:
    note = vault_root / "Core" / "Preferences.md"
    note.write_text(CORE + "- [x] “Quoted” → [[X]] · a ^lh-mem-1\n", encoding="utf-8")
    r = await app_client.post(
        "/edit",
        json={"path": "Core/Preferences.md", "old": "^lh-mem-1", "new": "", "whole_line": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["detail"] == "line deleted"
    assert "lh-mem-1" not in note.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_append_does_not_double_the_heading_marker(
    app_client: AsyncClient, vault_root: Path
) -> None:
    r = await app_client.post(
        "/append", json={"path": "Core/Preferences.md", "heading": "## Tools", "content": "x"}
    )
    assert r.status_code == 200, r.text
    text = (vault_root / "Core" / "Preferences.md").read_text(encoding="utf-8")
    assert "\n## Tools (" in text and "## ##" not in text
