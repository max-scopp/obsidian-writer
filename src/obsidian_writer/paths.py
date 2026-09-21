"""Path safety.

The single invariant this module defends is:

    The vault root is the only place the service is allowed to read or write.

Path traversal is the most reliable way to escape the vault — a maliciously
crafted `path` like `../../etc/passwd` must never reach the filesystem.
Symlinks inside the vault could be used to point outside, so we canonicalise
through `Path.resolve(strict=False)` and assert the result is still under the
configured vault root.

There is intentionally **no allowlist of subfolders**. The agent picks where
to write; this module only checks "is this path safely inside the vault?"
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# Files the agent must never touch directly, even if it asks nicely.
RESERVED_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {
        ".obsidian-map.yaml",  # the agent's own structure cache — see map.py
        ".trash",  # soft-delete staging directory
        ".obsidian",  # Obsidian's own config (we never write here)
    }
)


class PathError(Exception):
    """Base error for path resolution problems."""


class PathTraversalError(PathError):
    """Raised when a request path resolves outside the vault root."""


class ReservedPathError(PathError):
    """Raised when a request targets a reserved top-level file/directory."""


def safe_resolve(vault_root: Path, requested: str) -> Path:
    """Resolve a vault-relative path against the vault root, refusing escape.

    Args:
        vault_root: absolute path to the mounted vault root.
        requested: a path string. Absolute paths are rejected (the caller must
            always send vault-relative paths). Paths containing null bytes or
            other control characters are rejected. `..` segments are allowed
            syntactically but the *resolved* path must remain inside the vault.

    Returns:
        The canonical, absolute path.

    Raises:
        PathTraversalError: if the resolved path falls outside the vault root,
            or if the input contains suspicious characters, or if the input is
            absolute, or if a symlink resolves outside the vault.
        ReservedPathError: if the resolved path targets a reserved top-level
            entry (`.obsidian-map.yaml`, `.trash/`, `.obsidian/`).
    """
    if not requested:
        raise PathTraversalError("path must be non-empty")

    if "\x00" in requested:
        raise PathTraversalError("path must not contain null bytes")

    if requested.startswith(("/", "\\")) or (
        len(requested) >= 2 and requested[1] == ":"
    ):
        # POSIX absolute (/x), UNC (\x), or Windows drive (C:\x)
        raise PathTraversalError(
            f"absolute paths are not allowed: {requested!r}"
        )

    root = vault_root.resolve(strict=False)
    candidate = (root / requested).resolve(strict=False)

    # Containment check. The relative form of `candidate` against the root
    # must not start with `..` — that's how pathlib tells us we escaped.
    try:
        rel = candidate.relative_to(root)
    except ValueError as exc:
        raise PathTraversalError(
            f"path resolves outside vault: {requested!r}"
        ) from exc

    # Reject reserved top-level entries by the relative path's first segment.
    # We also strip any trailing extension from the first segment so that
    # `.obsidian-map.yaml.md` (which would otherwise pass) is still caught —
    # the auto-`.md` append in the create endpoint must not let an agent
    # write a "near-miss" that shadows the agent's own state.
    if rel.parts:
        first = rel.parts[0]
        if first in RESERVED_TOP_LEVEL:
            raise ReservedPathError(f"path targets reserved entry: {first}")
        first_stem = first.rsplit(".", 1)[0] if "." in first else first
        if first_stem in RESERVED_TOP_LEVEL:
            raise ReservedPathError(f"path targets reserved entry: {first_stem}")

    return candidate


def to_vault_relative(vault_root: Path, absolute: Path) -> str:
    """Convert an absolute path back to a vault-relative POSIX string."""
    return absolute.relative_to(vault_root.resolve(strict=False)).as_posix()


def safe_filename(name: str) -> str:
    """Sanitise a user-supplied note title into a safe filename stem.

    Rules (intentionally conservative):
        - Lowercase
        - Replace runs of non-alphanumeric with single `-`
        - Strip leading/trailing dashes
        - Limit to 96 characters
        - Reject names that would be empty after sanitisation
    """
    lowered = name.lower()
    cleaned = []
    last_was_dash = True  # suppress leading dash
    for ch in lowered:
        if ch.isascii() and (ch.isalnum() or ch in ".-_"):
            cleaned.append(ch)
            last_was_dash = ch == "-"
        elif not last_was_dash:
            cleaned.append("-")
            last_was_dash = True
    stem = "".join(cleaned).strip("-")
    if not stem:
        raise PathError(f"filename sanitised to empty: {name!r}")
    return stem[:96]
