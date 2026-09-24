"""Endpoint implementations.

Endpoints are intentionally short — each delegates to a focused helper.
The two cross-cutting concerns (path safety, rate limit) are enforced
in the FastAPI dependencies in `app.py` and the helper functions in
`paths.py`/`ratelimit.py`. This module is where the vault-specific
semantics live.

Endpoint map (see README.md):
  GET    /note          - read one note
  GET    /canvas        - read one canvas (*.canvas)
  PUT    /canvas        - create or replace a canvas
  GET    /list          - list a folder
  GET    /search        - search by title/tag/path substring
  POST   /create        - create a new note
  POST   /append        - append a section to an existing note
  POST   /edit          - replace one exact passage of a note
  PUT    /section       - replace (or add) the section under a heading
  PATCH  /frontmatter   - patch typed frontmatter fields
  POST   /trash         - soft-delete (move to .trash/YYYY-MM-DD/)
  GET    /map           - read .obsidian-map.yaml
  PATCH  /map           - patch .obsidian-map.yaml
  POST   /snapshot      - record hand edits made in Obsidian
  GET    /history       - commits touching the vault or one note
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from . import canvas as canvasmod
from . import edit
from . import map as mapmod
from .deps import (
    actor,
    get_vault,
    rate_limited,
    require_read_token,
    require_write_token,
)
from .frontmatter import (
    parse_frontmatter,
    patch_frontmatter_yaml,
    render_created,
    render_vault_created,
    utc_now_iso,
)
from .history import History
from .io import atomic_write, iter_markdown
from .models import (
    AppendRequest,
    CanvasContent,
    CanvasWriteRequest,
    CreateRequest,
    EditRequest,
    FrontmatterPatchRequest,
    HistoryEntry,
    HistoryResult,
    ListResult,
    MapPatchRequest,
    NoteContent,
    NoteSummary,
    SearchResult,
    SectionRequest,
    SnapshotRequest,
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


def _history(request: Request) -> History | None:
    history: History | None = request.app.state.history
    return history


async def _checkpoint(request: Request, *paths: str) -> None:
    """Commit hand edits in `paths` so the coming write is credited alone."""
    history = _history(request)
    if history:
        await asyncio.to_thread(history.checkpoint, list(paths))


async def _record(request: Request, who: str, message: str, *paths: str) -> None:
    history = _history(request)
    if history:
        await asyncio.to_thread(history.record, list(paths), message, who)


def _require_note(vault_root: Path, requested: str) -> Path:
    target = _require_safe_path(vault_root, requested)
    if not target.is_file() or target.suffix.lower() != ".md":
        raise HTTPException(status_code=404, detail=f"no markdown note at {requested!r}")
    return target


def _require_sha(target: Path, expected: str | None) -> None:
    if expected and _sha256(target) != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the note changed since it was read; read it again before editing",
        )


def _canvas_path(path: str) -> str:
    """Append the .canvas extension when the caller left it off."""
    return path if path.lower().endswith(".canvas") else path + ".canvas"


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


@router.get("/canvas", response_model=CanvasContent)
async def get_canvas(
    path: Annotated[str, Query(min_length=1, max_length=512)],
    vault_root: Path = Depends(get_vault),
    _token: str = Depends(require_read_token),
) -> CanvasContent:
    target = _require_safe_path(vault_root, _canvas_path(path))
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"no canvas at {path!r}")

    try:
        doc = canvasmod.loads(target.read_text(encoding="utf-8"))
    except canvasmod.CanvasError as exc:
        # The file on disk is broken; say so rather than returning half a canvas.
        raise HTTPException(status_code=422, detail=f"canvas is malformed: {exc}") from exc

    return CanvasContent(
        path=to_vault_relative(vault_root, target),
        nodes=doc.get("nodes", []),
        edges=doc.get("edges", []),
        sha=_sha256(target),
    )


@router.put("/canvas", response_model=WriteResult)
async def put_canvas(
    req: CanvasWriteRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
) -> WriteResult:
    _check_write_rate(request, token)
    target = _require_safe_path(vault_root, _canvas_path(req.path))

    doc = {"nodes": req.nodes, "edges": req.edges}
    try:
        canvasmod.validate(doc)
    except canvasmod.CanvasError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    existed = target.is_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, canvasmod.dumps(doc))

    return WriteResult(
        kind="updated" if existed else "created",
        path=to_vault_relative(vault_root, target),
        sha=_sha256(target),
        detail=f"{len(req.nodes)} nodes, {len(req.edges)} edges",
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
    who: str = Depends(actor),
) -> WriteResult:
    _check_write_rate(request, token)
    if req.path.lower().endswith(".canvas"):
        # /create renders Markdown frontmatter; writing that into a canvas
        # produces a file Obsidian cannot open.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="use PUT /canvas for .canvas files",
        )
    requested = req.path if req.path.lower().endswith(".md") else req.path + ".md"

    target = _require_safe_path(vault_root, requested)
    if target.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a note already exists at {req.path!r}",
        )

    title = req.title or target.stem
    if req.frontmatter:
        body = render_vault_created(title, req.frontmatter.model_dump(), body=req.body)
    else:
        body = render_created(
            title=title,
            tags=req.tags,
            conversation=req.conversation,
            model=req.model,
            body=req.body,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, body)
    rel = to_vault_relative(vault_root, target)
    await _record(request, who, f"Create {rel}", rel)

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
    who: str = Depends(actor),
) -> WriteResult:
    _check_write_rate(request, token)
    target = _require_note(vault_root, req.path)
    rel = to_vault_relative(vault_root, target)
    await _checkpoint(request, rel)

    block, body = edit.split_note(target.read_text(encoding="utf-8"))
    stamp = utc_now_iso()
    heading = req.heading.lstrip("#").strip() or req.heading  # models do send "## Title"
    new_body = body.rstrip() + f"\n\n## {heading} ({stamp})\n\n{req.content}\n"
    atomic_write(target, edit.touch(block) + new_body)
    await _record(request, who, f"Append to {rel}: {heading}", rel)

    return WriteResult(
        kind="appended",
        path=to_vault_relative(vault_root, target),
        sha=_sha256(target),
        detail=None,
    )


@router.post("/edit", response_model=WriteResult)
async def edit_note(
    req: EditRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
    who: str = Depends(actor),
) -> WriteResult:
    """Correct a note in place: replace one exact passage of its body."""
    _check_write_rate(request, token)
    target = _require_note(vault_root, req.path)
    _require_sha(target, req.expect_sha)
    rel = to_vault_relative(vault_root, target)
    await _checkpoint(request, rel)

    block, body = edit.split_note(target.read_text(encoding="utf-8"))
    try:
        if req.whole_line:
            new_body, deleted = edit.replace_line(body, req.old, req.new)
            detail = "line deleted" if deleted else "line replaced"
        else:
            new_body, count = edit.replace_text(
                body, req.old, req.new, replace_all=req.replace_all
            )
            detail = f"{count} replacement{'s' if count != 1 else ''}"
    except edit.EditError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    atomic_write(target, edit.touch(block) + new_body)
    await _record(request, who, f"Edit {rel}", rel)
    return WriteResult(kind="updated", path=rel, sha=_sha256(target), detail=detail)


@router.put("/section", response_model=WriteResult)
async def put_section(
    req: SectionRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
    who: str = Depends(actor),
) -> WriteResult:
    """Rewrite the section under a heading, or add it when missing."""
    _check_write_rate(request, token)
    target = _require_note(vault_root, req.path)
    _require_sha(target, req.expect_sha)
    rel = to_vault_relative(vault_root, target)
    await _checkpoint(request, rel)

    block, body = edit.split_note(target.read_text(encoding="utf-8"))
    try:
        new_body, created = edit.replace_section(
            body,
            req.heading,
            req.content,
            level=req.level,
            create=req.create,
            append=req.mode == "append",
        )
    except edit.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except edit.EditError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    atomic_write(target, edit.touch(block) + new_body)
    if created:
        verb, detail = "Add section", "section added"
    elif req.mode == "append":
        verb, detail = "Append to section", "appended to section"
    else:
        verb, detail = "Rewrite section", "section replaced"
    await _record(request, who, f"{verb} {req.heading!r} in {rel}", rel)
    return WriteResult(kind="updated", path=rel, sha=_sha256(target), detail=detail)


@router.patch("/frontmatter", response_model=WriteResult)
async def patch_frontmatter(
    req: FrontmatterPatchRequest,
    request: Request,
    vault_root: Path = Depends(get_vault),
    token: str = Depends(require_write_token),
    who: str = Depends(actor),
) -> WriteResult:
    _check_write_rate(request, token)
    target = _require_note(vault_root, req.path)
    rel = to_vault_relative(vault_root, target)
    await _checkpoint(request, rel)

    text = target.read_text(encoding="utf-8")
    try:
        new_text = patch_frontmatter_yaml(text, req.patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    atomic_write(target, new_text)
    await _record(request, who, f"Update frontmatter of {rel}: {', '.join(req.patch)}", rel)
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
    who: str = Depends(actor),
) -> WriteResult:
    _check_write_rate(request, token)
    target = _require_safe_path(vault_root, req.path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"no file at {req.path!r}")
    original = to_vault_relative(vault_root, target)
    await _checkpoint(request, original)

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
    reason = (req.reason or "").replace("\n", " ").strip()[:120]
    await _record(request, who, f"Trash {original}" + (f": {reason}" if reason else ""), original)

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
    who: str = Depends(actor),
) -> WriteResult:
    _check_write_rate(request, token)
    await _checkpoint(request, mapmod.MAP_FILENAME)
    merged = mapmod.patch_map(vault_root, req.patch)
    message = f"Update the vault map: {', '.join(req.patch)}"
    await _record(request, who, message, mapmod.MAP_FILENAME)
    return WriteResult(
        kind="patched",
        path=mapmod.MAP_FILENAME,
        sha=None,
        detail=f"{len(merged.get('folders', {}))} folders, "
        f"{len(merged.get('rules', []))} rules",
    )


# ----------------------------------------------------------------------------
# History
# ----------------------------------------------------------------------------


@router.post("/snapshot", response_model=WriteResult)
async def snapshot(
    request: Request,
    req: SnapshotRequest | None = None,
    _token: str = Depends(require_write_token),
) -> WriteResult:
    """Record every change made outside this service, as a hand edit.

    Not rate-limited: it is idempotent, cheap, and meant for a timer.
    """
    history = _history(request)
    if history is None:
        raise HTTPException(status_code=404, detail="vault history is not enabled")
    message = req.message if req else "Edit in Obsidian"
    commit = await asyncio.to_thread(history.snapshot, message)
    detail = None if commit else "no changes"
    return WriteResult(kind="recorded", path="", sha=commit, detail=detail)


@router.get("/history", response_model=HistoryResult)
async def get_history(
    request: Request,
    path: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    vault_root: Path = Depends(get_vault),
    _token: str = Depends(require_read_token),
) -> HistoryResult:
    """Who changed the vault (or one note), when, and with what message."""
    history = _history(request)
    if history is None:
        raise HTTPException(status_code=404, detail="vault history is not enabled")
    rel = to_vault_relative(vault_root, _require_safe_path(vault_root, path)) if path else None
    entries = await asyncio.to_thread(history.log, rel, limit)
    return HistoryResult(path=rel, entries=[HistoryEntry(**e) for e in entries])
