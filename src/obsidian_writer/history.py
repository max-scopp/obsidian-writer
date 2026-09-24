"""Vault history: one git commit per write, attributed to whoever wrote.

Several agents write to the vault - chat, automations, the memory bridge -
and the user edits it by hand in Obsidian. History answers "who changed
this, and what did it say before?", and makes a bad write one revert.

The repository lives *outside* the vault (`GIT_DIR` separate from the work
tree), so no `.git` directory is ever synced to the user's devices, and the
vault itself stays a plain folder of notes.

Attribution rules:

- A write through this service commits only the paths it touched, authored
  by the writing client (`litellm/lobehub`, `memory-bridge`, ...).
- Before that write, any unrecorded change already sitting in those same
  paths is committed first as an edit by `obsidian` - the user's hand edit
  must not be credited to the agent that happened to write next.
- `snapshot()` sweeps up every other unrecorded change the same way; call
  it periodically so hand edits get their own commits.

Failures here never fail a write: the note on disk is the source of truth,
and a missed commit is caught by the next snapshot.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("obsidian_writer.history")

HAND_EDIT_AUTHOR = "obsidian"

# Kept out of history: Obsidian's own UI state, the soft-delete bin, and
# half-written temp files from `atomic_write`.
EXCLUDES = (".obsidian/", ".trash/", "*.tmp.*", ".DS_Store")

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._/@-]+")


def author_name(actor: str) -> str:
    """Normalise a free-form actor label into a git author name."""
    name = _SAFE_NAME.sub("-", actor).strip("-")[:64]
    return name or "unknown"


class History:
    def __init__(self, git_dir: Path, work_tree: Path) -> None:
        self.git_dir = git_dir
        self.work_tree = work_tree
        self._lock = threading.Lock()

    # -- setup ---------------------------------------------------------------

    def ensure(self) -> None:
        """Create the repository on first start and record the vault as-is."""
        with self._lock:
            if not (self.git_dir / "HEAD").exists():
                self.git_dir.mkdir(parents=True, exist_ok=True)
                self._git("init", "--quiet")
                self._git("config", "commit.gpgsign", "false")
                log.info("initialised vault history at %s", self.git_dir)
            info = self.git_dir / "info"
            info.mkdir(exist_ok=True)
            (info / "exclude").write_text("\n".join(EXCLUDES) + "\n", encoding="utf-8")
        self.snapshot("Initial import of the vault", HAND_EDIT_AUTHOR)

    # -- writes --------------------------------------------------------------

    def record(self, paths: list[str], message: str, actor: str) -> str | None:
        """Commit the given vault-relative paths as written by `actor`."""
        with self._lock:
            return self._commit(paths, message, actor)

    def checkpoint(self, paths: list[str]) -> str | None:
        """Commit unrecorded hand edits in `paths` before a service write."""
        with self._lock:
            return self._commit(paths, "Edit in Obsidian", HAND_EDIT_AUTHOR)

    def snapshot(self, message: str, actor: str = HAND_EDIT_AUTHOR) -> str | None:
        """Commit every unrecorded change in the vault."""
        with self._lock:
            return self._commit(None, message, actor)

    # -- reads ---------------------------------------------------------------

    def log(self, path: str | None, limit: int) -> list[dict[str, str]]:
        args = ["log", f"-n{limit}", "--format=%H%x1f%an%x1f%aI%x1f%s"]
        if path:
            args += ["--follow", "--", path]
        with self._lock:
            if not self._has_commits():
                return []
            out = self._git(*args).stdout
        entries = []
        for line in out.splitlines():
            commit, author, date, subject = line.split("\x1f", 3)
            entries.append({"commit": commit, "author": author, "date": date, "message": subject})
        return entries

    # -- internals -------------------------------------------------------------

    def _commit(self, paths: list[str] | None, message: str, actor: str) -> str | None:
        try:
            if paths is not None:
                # A path that neither exists nor was ever recorded (the target
                # of a create, checkpointed before it is written) has nothing
                # to commit, and git would reject it as an unmatched pathspec.
                paths = [p for p in paths if (self.work_tree / p).exists() or self._tracked(p)]
                if not paths:
                    return None
            spec = ["--", *paths] if paths else []
            self._git("add", "--all", *spec)
            staged = self._git("diff", "--cached", "--quiet", *spec, check=False)
            if staged.returncode == 0:
                return None  # nothing changed in these paths
            name = author_name(actor)
            self._git(
                "-c",
                f"user.name={name}",
                "-c",
                f"user.email={name}@obsidian-writer",
                "commit",
                "--quiet",
                "--no-verify",
                "-m",
                message,
                *spec,
            )
            return self._git("rev-parse", "HEAD").stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or exc
            log.warning("history commit failed (%s): %s", message, detail)
            return None

    def _tracked(self, path: str) -> bool:
        return self._git("ls-files", "--error-unmatch", "--", path, check=False).returncode == 0

    def _has_commits(self) -> bool:
        return self._git("rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "GIT_DIR": str(self.git_dir),
            "GIT_WORK_TREE": str(self.work_tree),
            "GIT_TERMINAL_PROMPT": "0",
            # Note names are paths, not patterns: `[draft] *ideas*.md` is a file.
            "GIT_LITERAL_PATHSPECS": "1",
        }
        return subprocess.run(
            ["git", *args],
            cwd=self.work_tree,
            env=env,
            check=check,
            capture_output=True,
            text=True,
            timeout=60,
        )
