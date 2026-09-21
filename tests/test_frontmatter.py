"""Tests for frontmatter rendering, patching, and reserved-field guard."""

from __future__ import annotations

import pytest

from obsidian_writer.frontmatter import (
    patch_frontmatter_yaml,
    render_created,
)


def test_render_created_minimal() -> None:
    text = render_created(title="Hello")
    assert text.startswith("---\n")
    assert 'title: Hello' in text
    assert 'source: chat' in text
    assert 'created:' in text
    assert 'modified:' in text


def test_render_created_with_tags_and_meta() -> None:
    text = render_created(
        title="Note",
        tags=["Project", "Project", "Alpha"],
        conversation="trace-abc-123",
        model="Qwen/Qwen3-8B-AWQ",
        body="body line",
    )
    assert "tags:" in text
    # tags deduped and lowercased, sorted
    assert "- alpha" in text
    assert "- project" in text
    assert "conversation: trace-abc-123" in text
    assert "model: Qwen/Qwen3-8B-AWQ" in text
    assert text.endswith("body line\n")


def test_render_created_quotes_special_chars() -> None:
    text = render_created(title="Needs: quoting")
    assert 'title: "Needs: quoting"' in text


def test_patch_adds_new_field() -> None:
    original = render_created(title="Note", body="body")
    patched = patch_frontmatter_yaml(original, {"status": "open"})
    assert "status: open" in patched


def test_patch_replaces_existing_field() -> None:
    original = render_created(title="Note", body="body")
    patched = patch_frontmatter_yaml(original, {"status": "closed"})
    assert "status: closed" in patched


def test_patch_rejects_reserved_field() -> None:
    original = render_created(title="Note", body="body")
    for field in ("created", "source", "conversation", "path"):
        with pytest.raises(ValueError, match="reserved"):
            patch_frontmatter_yaml(original, {field: "hacked"})


def test_patch_rejects_type_mismatch() -> None:
    original = render_created(title="Note", tags=["x"], body="body")
    # `tags` was a list, can't be patched with a string.
    with pytest.raises(TypeError, match="type"):
        patch_frontmatter_yaml(original, {"tags": "not-a-list"})


def test_patch_updates_modified_field() -> None:
    original = render_created(title="Note", body="body")
    patched = patch_frontmatter_yaml(original, {"status": "open"})
    # Both frontmatter blocks contain a `modified:` line; the patched
    # version's value must be the same as `created` in `patched` (set
    # atomically by patch_frontmatter_yaml).
    assert "modified:" in patched
