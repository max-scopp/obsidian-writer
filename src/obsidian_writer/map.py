"""The agent's structure cache: `.obsidian-map.yaml`.

This module owns the *shape* of the cache and the safe defaults the agent
starts with when the vault is empty. The contents are entirely agent-owned
after first write; this module never overwrites them, only reads and
merges per the agent's PATCH requests.

Schema (informal):

    version: 1
    folders:
      <path>:
        kind: inbox | journal | reference | project | area | resource | trash | unknown
        purpose: <one-sentence plain-language description>
        writable: true | false
        examples:
          - "<note path that's representative>"
        confidence: 0.0..1.0   # grows as the agent sees more examples
        last_updated: <iso8601>
    rules:
      - "<invariant the agent has learned about this vault>"
    aliases:
      journal: ["daily", "diary", "log"]
      project: ["projects", "work"]

The first time the agent writes to this vault, the file is created with
the `INITIAL_MAP` defaults below. The agent updates it from there.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import safe_yaml_dump
from .io import atomic_write

MAP_FILENAME = ".obsidian-map.yaml"

INITIAL_MAP: dict[str, Any] = {
    "version": 1,
    "folders": {
        "Inbox": {
            "kind": "inbox",
            "purpose": "Default landing pad for unfiled notes",
            "writable": True,
            "examples": [],
            "confidence": 0.5,
            "last_updated": "2026-01-01T00:00:00Z",
        },
        "Daily": {
            "kind": "journal",
            "purpose": "Day-by-day journal, one file per day",
            "writable": True,
            "examples": [],
            "confidence": 0.5,
            "last_updated": "2026-01-01T00:00:00Z",
        },
    },
    "rules": [
        "Default to Inbox when user gives no destination",
        "Default to Daily for time-anchored entries (today, this morning)",
        "Ask once when destination is ambiguous; remember the answer",
    ],
    "aliases": {},
}


def map_path(vault_root: Path) -> Path:
    return vault_root / MAP_FILENAME


def read_map(vault_root: Path) -> dict[str, Any]:
    """Read the structure map; create it with defaults if absent."""
    path = map_path(vault_root)
    if not path.exists():
        # Avoid importing yaml at module scope in this hot path; import lazily.
        import yaml

        atomic_write(path, safe_yaml_dump(INITIAL_MAP))
        # Refresh the `last_updated` field so the file reflects actual load time.
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for entry in INITIAL_MAP["folders"].values():
            entry["last_updated"] = stamp
        atomic_write(path, safe_yaml_dump(INITIAL_MAP))
        return _deep_copy_defaults()

    import yaml

    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    return _normalise_shape(loaded)


def write_map(vault_root: Path, data: dict[str, Any]) -> None:
    """Atomically replace the on-disk map."""
    path = map_path(vault_root)
    payload = _normalise_shape(data)
    atomic_write(path, safe_yaml_dump(payload))


def patch_map(vault_root: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a shallow merge patch and persist.

    Special keys handled:
        - `folders`: dict of `<path>: <entry>`. Entries are deep-merged per
          folder, so the agent can update one field without clobbering the
          rest.
        - `rules`: list. Replaced entirely if a list is given.
        - `aliases`: dict. Replaced entirely if a dict is given.
        - Any other top-level key: replaced.

    Returns the post-merge map.
    """

    current = read_map(vault_root)
    merged = _merge(current, patch)
    write_map(vault_root, merged)
    return merged


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Deep merge `patch` onto `base`, with per-key rules described above."""
    out = dict(base)

    for key, value in patch.items():
        if key == "folders" and isinstance(value, dict):
            existing = out.get("folders", {})
            if not isinstance(existing, dict):
                existing = {}
            merged_folders = dict(existing)
            for folder_path, folder_patch in value.items():
                prev = merged_folders.get(folder_path, {})
                if isinstance(prev, dict) and isinstance(folder_patch, dict):
                    new_entry = dict(prev)
                    new_entry.update(folder_patch)
                    new_entry["last_updated"] = datetime.now(UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    merged_folders[folder_path] = new_entry
                else:
                    merged_folders[folder_path] = folder_patch
            out["folders"] = merged_folders
        elif key in ("rules", "aliases"):
            # Lists and dicts at the top level are replaced wholesale.
            out[key] = value
        else:
            out[key] = value

    return out


def _normalise_shape(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure the map has the expected top-level keys with sensible defaults."""
    out: dict[str, Any] = {
        "version": int(data.get("version", 1)),
        "folders": dict(data.get("folders") or {}),
        "rules": list(data.get("rules") or []),
        "aliases": dict(data.get("aliases") or {}),
    }
    return out


def _deep_copy_defaults() -> dict[str, Any]:
    import copy

    return copy.deepcopy(INITIAL_MAP)
