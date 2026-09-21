"""Atomic filesystem writes.

A write to a path the user actually edits should never half-happen — if the
process is killed, the network drops, or the NFS server is briefly
unreachable mid-write, the existing note must remain intact.

Pattern: write to `<path>.tmp.<random>`, fsync the data, rename onto the
final path. On NFS-over-ZFS (the expected mount) `rename(2)` is atomic.
The temporary file is unlinked on success and on failure.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)


def atomic_write(target: Path, data: bytes | str) -> None:
    """Write `data` to `target` atomically.

    `data` may be bytes or str. If str, it is encoded as UTF-8.

    Raises:
        OSError: on any filesystem failure. The target path is guaranteed to
            be either unchanged (write failed before rename) or fully replaced
            with `data` (rename succeeded). It will never be partially written.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")

    target.parent.mkdir(parents=True, exist_ok=True)

    # The .tmp suffix is followed by 8 random hex chars; on collision the
    # rename below will overwrite the tmp file before its rename, which is
    # fine — we only collide if two writers target the same `target` at once.
    tmp = target.with_suffix(target.suffix + f".tmp.{secrets.token_hex(4)}")

    try:
        with open(tmp, "xb", buffering=0) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        # Best-effort cleanup of the tmp file. If this fails, the next
        # writer to the same target will overwrite it; if it doesn't, the
        # tmp file lingers harmlessly (and the boot-time janitor in
        # `cleanup_stale_tmp()` catches it later).
        import contextlib

        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise


def cleanup_stale_tmp(vault_root: Path, max_age_seconds: int = 86400) -> int:
    """Remove leftover `*.tmp.*` files older than `max_age_seconds`.

    Returns the number of files removed. Intended to run on service start.
    """
    cutoff = max_age_seconds
    now = __import__("time").time()
    removed = 0
    for tmp in vault_root.rglob("*.tmp.*"):
        try:
            age = now - tmp.stat().st_mtime
        except FileNotFoundError:
            continue
        if age >= cutoff:
            try:
                tmp.unlink()
                removed += 1
            except OSError as exc:
                log.warning("could not remove stale tmp %s: %s", tmp, exc)
    return removed


def iter_markdown(vault_root: Path) -> Iterable[Path]:
    """Yield all `*.md` files under the vault, skipping reserved dirs."""
    from .paths import RESERVED_TOP_LEVEL  # avoid circular import

    for path in vault_root.rglob("*.md"):
        try:
            rel = path.relative_to(vault_root).parts
        except ValueError:
            continue
        if rel and rel[0] in RESERVED_TOP_LEVEL:
            continue
        yield path
