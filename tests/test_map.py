"""Tests for the agent's structure cache."""

from __future__ import annotations

from pathlib import Path

import yaml

from obsidian_writer import map as mapmod


def test_read_map_creates_defaults(tmp_path: Path) -> None:
    result = mapmod.read_map(tmp_path)
    assert "folders" in result
    assert "Inbox" in result["folders"]
    assert "Daily" in result["folders"]
    # And the file is now on disk
    on_disk = yaml.safe_load((tmp_path / mapmod.MAP_FILENAME).read_text())
    assert on_disk["folders"]["Inbox"]["writable"] is True


def test_patch_map_merges_folders(tmp_path: Path) -> None:
    mapmod.read_map(tmp_path)  # seed defaults
    result = mapmod.patch_map(
        tmp_path,
        {
            "folders": {
                "Projects": {
                    "kind": "project",
                    "purpose": "Active project notes",
                    "writable": True,
                    "examples": ["Projects/foo.md"],
                    "confidence": 0.8,
                }
            },
            "rules": ["Always create a project folder entry on first write"],
        },
    )
    assert "Projects" in result["folders"]
    assert "Inbox" in result["folders"]  # defaults preserved
    assert result["rules"] == ["Always create a project folder entry on first write"]


def test_patch_map_deep_merges_folder_entry(tmp_path: Path) -> None:
    mapmod.read_map(tmp_path)  # seed Inbox with confidence 0.5
    result = mapmod.patch_map(
        tmp_path,
        {"folders": {"Inbox": {"confidence": 0.9, "purpose": "Definitely the inbox"}}},
    )
    assert result["folders"]["Inbox"]["confidence"] == 0.9
    assert result["folders"]["Inbox"]["purpose"] == "Definitely the inbox"
    # other defaults preserved
    assert result["folders"]["Inbox"]["writable"] is True


def test_patch_map_replaces_rules_list(tmp_path: Path) -> None:
    mapmod.read_map(tmp_path)
    result = mapmod.patch_map(tmp_path, {"rules": ["only one rule now"]})
    assert result["rules"] == ["only one rule now"]


def test_write_map_persists_atomically(tmp_path: Path) -> None:
    custom = {
        "version": 1,
        "folders": {"X": {"kind": "unknown", "purpose": "X", "writable": True}},
        "rules": [],
        "aliases": {},
    }
    mapmod.write_map(tmp_path, custom)
    on_disk = yaml.safe_load((tmp_path / mapmod.MAP_FILENAME).read_text())
    assert "X" in on_disk["folders"]
