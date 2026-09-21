"""Tests for the path safety module."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_writer.paths import (
    PathTraversalError,
    ReservedPathError,
    safe_filename,
    safe_resolve,
    to_vault_relative,
)


def test_resolve_simple_relative_path(tmp_path: Path) -> None:
    target = safe_resolve(tmp_path, "foo/bar.md")
    assert target == (tmp_path / "foo" / "bar.md").resolve(strict=False)


def test_resolve_rejects_null_bytes(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError, match="null bytes"):
        safe_resolve(tmp_path, "foo\x00bar.md")


def test_resolve_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError, match="absolute"):
        safe_resolve(tmp_path, "/etc/passwd")


def test_resolve_rejects_drive_letter(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError, match="absolute"):
        safe_resolve(tmp_path, "C:\\Windows\\system32")


def test_resolve_rejects_double_dot_traversal(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError, match="outside vault"):
        safe_resolve(tmp_path, "../../../etc/passwd")


def test_resolve_rejects_subtle_traversal(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError, match="outside vault"):
        safe_resolve(tmp_path, "foo/../../escape.md")


def test_resolve_allows_dotdot_that_stays_inside(tmp_path: Path) -> None:
    """`a/b/../c.md` should resolve to `a/c.md` and be allowed."""
    target = safe_resolve(tmp_path, "a/b/../c.md")
    assert target == (tmp_path / "a" / "c.md").resolve(strict=False)


def test_resolve_rejects_reserved_top_level(tmp_path: Path) -> None:
    with pytest.raises(ReservedPathError, match=".obsidian-map.yaml"):
        safe_resolve(tmp_path, ".obsidian-map.yaml")


def test_resolve_rejects_trash_top_level(tmp_path: Path) -> None:
    with pytest.raises(ReservedPathError, match=".trash"):
        safe_resolve(tmp_path, ".trash/2026-09-21/foo.md")


def test_resolve_rejects_obsidian_top_level(tmp_path: Path) -> None:
    with pytest.raises(ReservedPathError, match=".obsidian"):
        safe_resolve(tmp_path, ".obsidian/workspace.json")


def test_resolve_allows_reserved_name_in_subfolder(tmp_path: Path) -> None:
    """A note named `.obsidian` inside Inbox/ is fine — only top-level is reserved."""
    target = safe_resolve(tmp_path, "Inbox/.obsidian/note.md")
    assert target == (tmp_path / "Inbox" / ".obsidian" / "note.md").resolve(strict=False)


def test_resolve_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError, match="non-empty"):
        safe_resolve(tmp_path, "")


def test_safe_filename_lowercases_and_dashes() -> None:
    assert safe_filename("My First Note!") == "my-first-note"


def test_safe_filename_strips_specials() -> None:
    assert safe_filename("v2.0 (beta)") == "v2.0-beta"


def test_safe_filename_rejects_empty_after_sanitise() -> None:
    with pytest.raises(Exception, match="empty"):
        safe_filename("!!!@@@")


def test_safe_filename_truncates_long_names() -> None:
    long = "a" * 200
    out = safe_filename(long)
    assert len(out) == 96


def test_to_vault_relative_round_trip(tmp_path: Path) -> None:
    rel = "Inbox/hello.md"
    abs_path = safe_resolve(tmp_path, rel)
    assert to_vault_relative(tmp_path, abs_path) == rel
