"""Tests for atomic writes and tmp cleanup."""

from __future__ import annotations

import os
from pathlib import Path

from obsidian_writer.io import atomic_write, cleanup_stale_tmp


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    atomic_write(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "note.md"
    atomic_write(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    atomic_write(target, "first")
    atomic_write(target, "second")
    assert target.read_text() == "second"


def test_atomic_write_unicode(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    atomic_write(target, "héllo 🌍")
    assert target.read_text(encoding="utf-8") == "héllo 🌍"


def test_atomic_write_no_leftover_tmp(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    atomic_write(target, "hello")
    leftovers = list(tmp_path.glob("*.tmp.*"))
    assert leftovers == []


def test_cleanup_stale_tmp_removes_old(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    atomic_write(target, "hello")
    # Manufacture a stale tmp by direct write + backdating mtime.
    stale = tmp_path / "note.md.tmp.abcdef01"
    stale.write_text("stale")
    old = tmp_path / "note.md.tmp.01234567"
    old.write_text("old")
    # backdate mtime by 2 days
    import time

    two_days_ago = time.time() - 86400 * 2
    os.utime(old, (two_days_ago, two_days_ago))

    removed = cleanup_stale_tmp(tmp_path, max_age_seconds=86400)
    assert removed == 1
    assert stale.exists()
    assert not old.exists()


def test_atomic_write_failure_leaves_target_intact(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    atomic_write(target, "original")

    # Replace the file with a *directory of the same name* — the rename
    # inside atomic_write will then fail (you can't rename a file over a
    # non-empty directory).
    target.unlink()
    target.mkdir()
    (target / "child").write_text("blocker")

    import pytest

    with pytest.raises(OSError):
        atomic_write(tmp_path / "note.md", "new")

    # The blocker file inside the directory must still be there.
    assert (tmp_path / "note.md" / "child").read_text() == "blocker"
    # And no leftover tmp file at the vault root.
    leftovers = list(tmp_path.glob("*.tmp.*"))
    assert leftovers == []
