"""Endpoint implementations.

Endpoints are intentionally short — each delegates to a focused helper.
The two cross-cutting concerns (path safety, rate limit) are enforced
in the FastAPI dependencies in `app.py` and the helper functions in
`paths.py`/`ratelimit.py`. This module is where the vault-specific
semantics live.

Endpoint map (see README.md):
  GET    /note          - read one note
  GET    /list          - list a folder
  GET    /search        - search by title/tag/path substring
  POST   /create        - create a new note
  POST   /append        - append a section to an existing note
  PATCH  /frontmatter   - patch typed frontmatter fields
  POST   /trash         - soft-delete (move to .trash/YYYY-MM-DD/)
  GET    /map           - read .obsidian-map.yaml
  PATCH  /map           - patch .obsidian-map.yaml
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from . import map as mapmod
from .deps import (
    get_vault,
    rate_limited,
    require_read_token,
    require_write_token,
)
from .frontmatter import (
    parse_frontmatter,
    patch_frontmatter_yaml,
    render_created,
    utc_now_iso,
)
from .io import atomic_write, iter_markdown
from .models import (
    AppendRequest,
    CreateRequest,
    FrontmatterPatchRequest,
    ListResult,
    MapPatchRequest,
    NoteContent,
    NoteSummary,
    SearchResult,
    TrashRequest,
    WriteResult,
)
from .paths import (
    PathTraversalError,
    ReservedPathError,
    safe_resolve,
    to_vault_relative,
)

log = logging.getLogger("obsidian_writer")
router = APIRouter()


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(path: Path, vault_root: Path) -> NoteSummary:
    rel = to_vault_relative(vault_root, path)
    text = path.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(text)
    return NoteSummary(
        path=rel,
        title=fm.get("title") if isinstance(fm.get("title"), str) else None,
        tags=fm.get("tags") if isinstance(fm.get("tags"), list) else [],
        size=path.stat().st_size,
        modified=datetime.fromtimestamp(
            path.stat().st_mtime, tz=UTC
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _require_safe_path(vault_root: Path, requested: str) -> Path:
    try:
        return safe_resolve(vault_root, requested)
    except PathTraversalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ReservedPathError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _check_write_rate(request: Request, token: str) -> None:
    """Forward to the limiter registered on the app state."""
    rate_limited(token, request)


# ----------------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------------


@router.get("/note", response_model=NoteContent)
async def get_note(
    path: Annotated[str, Query(min_length=1, max_length=512)],
    vault_root: Path = Depends(get_vault),
    _token: str = Depends(require_read_token),
) -> NoteContent:
    target = _require_safe_path(vault_root, path)
    if not target.is_file() or target.suffix.lower() != ".md":
        raise HTTPException(status_code=404, detail=f"no markdown note at {path!r}")

    fm, body = parse_frontmatter(target.read_text(encoding="utf-8"))
    return NoteContent(
        path=to_vault_relative(vault_root, target),
        title=fm.get("title") if isinstance(fm.get("title"), str) else None,
        tags=fm.get("tags") if isinstance(fm.get("tags"), list) else [],
        frontmatter=fm,
        body=body,
        sha=_sha256(target),
    )


@router.get("/list", response_model=ListResult)
async def list_folder(
    path: Annotated[str, Query(min_length=0, max_length=512)] = "",
    vault_root: Path = Depends(get_vault),
    _token: str = Depends(require_read_token),
) -> ListResult:
    target = _require_safe_path(vault_root, path) if path else vault_root
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"no such folder: {path!r}")

    notes: list[NoteSummary] = []
    folders: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith("."):
            # Hide hidden entries (`.obsidian`, `.trash`, etc.) from listings.
            continue
        if entry.is_dir():
            folders.append(entry.name)
        elif entry.is_file() and entry.suffix.lower() == ".md":
            notes.append(_summary(entry, vault_root))

    return ListResult(
        path=to_vault_relative(vault_root, target),
        notes=notes,
        folders=folders,
    )


@router.get("/search", response_model=SearchResult)
async def search_notes(
    q: Annotated[str, Query(min_length=1, max_length=200)],
    vault_root: Path = Depends(get_vault),
    _token: str = Depends(require_read_token),
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> SearchResult:
    q_lower = q.lower()
    matches: list[NoteSummary] = []
    for md in iter_markdown(vault_root):
        text = md.read_text(encoding="utf-8", errors="replace").lower()
        rel = to_vault_relative(vault_root, md)
        if q_lower in rel.lower() or q_lower in text:
            matches.append(_summary(md, vault_root))
            if len(matches) >= limit:
                break
    return SearchResult(matches=matches, total=len(matches))


# ----------------------------------------------------------------------------
# Writes (all require the write token and are rate-limited)
# ----------------------------------------------------------------------------


@router.post("/create", response_model=WriteResult, status_code=status.HTTP_201_CREATED)
async def create_note(
    req: CreateRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
) -> WriteResult:
    _check_write_rate(request, token)
    requested = req.path if req.path.lower().endswith(".md") else req.path + ".md"

    target = _require_safe_path(vault_root, requested)
    if target.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a note already exists at {req.path!r}",
        )

    title = req.title or target.stem
    body = render_created(
        title=title,
        tags=req.tags,
        conversation=req.conversation,
        model=req.model,
        body=req.body,
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, body)

    return WriteResult(
        kind="created",
        path=to_vault_relative(vault_root, target),
        sha=_sha256(target),
        detail=None,
    )


@router.post("/append", response_model=WriteResult)
async def append_to_note(
    req: AppendRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
) -> WriteResult:
    _check_write_rate(request, token)
    target = _require_safe_path(vault_root, req.path)
    if not target.is_file() or target.suffix.lower() != ".md":
        raise HTTPException(status_code=404, detail=f"no markdown note at {req.path!r}")

    text = target.read_text(encoding="utf-8")
    stamp = utc_now_iso()
    new_text = text.rstrip() + f"\n\n## {req.heading} ({stamp})\n\n{req.content}\n"
    atomic_write(target, new_text)

    return WriteResult(
        kind="appended",
        path=to_vault_relative(vault_root, target),
        sha=_sha256(target),
        detail=None,
    )


@router.patch("/frontmatter", response_model=WriteResult)
async def patch_frontmatter(
    req: FrontmatterPatchRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
) -> WriteResult:
    _check_write_rate(request, token)
    target = _require_safe_path(vault_root, req.path)
    if not target.is_file() or target.suffix.lower() != ".md":
        raise HTTPException(status_code=404, detail=f"no markdown note at {req.path!r}")

    text = target.read_text(encoding="utf-8")
    try:
        new_text = patch_frontmatter_yaml(text, req.patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    atomic_write(target, new_text)
    return WriteResult(
        kind="patched",
        path=to_vault_relative(vault_root, target),
        sha=_sha256(target),
        detail=None,
    )


@router.post("/trash", response_model=WriteResult, status_code=status.HTTP_201_CREATED)
async def trash_note(
    req: TrashRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
) -> WriteResult:
    _check_write_rate(request, token)
    target = _require_safe_path(vault_root, req.path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"no file at {req.path!r}")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    trash_dir = vault_root / ".trash" / today
    trash_dir.mkdir(parents=True, exist_ok=True)

    dest = trash_dir / target.name
    if dest.exists():
        stamp = datetime.now(UTC).strftime("%H%M%S")
        dest = trash_dir / f"{target.stem}-{stamp}{target.suffix}"

    target.rename(dest)

    meta = dest.with_suffix(dest.suffix + ".meta.md")
    meta_text = (
        "---\n"
        f"original_path: {to_vault_relative(vault_root, target)}\n"
        f"trashed_at: {utc_now_iso()}\n"
        f"reason: {(req.reason or '').replace(chr(10), ' ').strip()[:500]}\n"
        "---\n"
    )
    atomic_write(meta, meta_text)

    return WriteResult(
        kind="trashed",
        path=to_vault_relative(vault_root, dest),
        sha=_sha256(dest),
        detail=f"original: {req.path}",
    )


# ----------------------------------------------------------------------------
# Structure cache (`.obsidian-map.yaml`)
# ----------------------------------------------------------------------------


@router.get("/map")
async def get_map(
    vault_root: Path = Depends(get_vault),
    _token: str = Depends(require_read_token),
) -> dict:
    return mapmod.read_map(vault_root)


@router.patch("/map", response_model=WriteResult)
async def patch_map(
    req: MapPatchRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
) -> WriteResult:
    _check_write_rate(request, token)
    merged = mapmod.patch_map(vault_root, req.patch)
    return WriteResult(
        kind="patched",
        path=mapmod.MAP_FILENAME,
        sha=None,
        detail=f"{len(merged.get('folders', {}))} folders, "
        f"{len(merged.get('rules', []))} rules",
    )
