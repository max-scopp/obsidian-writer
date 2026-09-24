"""Request and response models for the writer API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NoteSummary(BaseModel):
    path: str
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    size: int
    modified: str  # ISO 8601 UTC


class NoteContent(BaseModel):
    path: str
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str
    sha: str  # sha256 of the on-disk file


class ListResult(BaseModel):
    path: str
    notes: list[NoteSummary]
    folders: list[str]


class SearchResult(BaseModel):
    matches: list[NoteSummary]
    total: int


class VaultFrontmatter(BaseModel):
    """The frontmatter contract of a vault that declares one in its map."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=40)
    scope: Literal["shared", "agent"] = "shared"
    agent: str | None = Field(default=None, max_length=60)
    topics: list[str] = Field(default_factory=list)
    status: str | None = Field(default=None, max_length=40)
    # Absent means confirmed first-hand; set it for anything inferred.
    source: Literal["reconstruction", "inferred"] | None = None


class CreateRequest(BaseModel):
    """Create a new note.

    `path` is the requested vault-relative location (no extension). The
    service resolves it against the vault root and rejects paths that
    escape or target reserved entries. If a note already exists at the
    resolved path, returns 409.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    title: str | None = Field(default=None, max_length=200)
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    conversation: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    # The vault's own frontmatter contract (see `.obsidian-map.yaml`). When
    # given, the note is written in that shape instead of the legacy
    # title/created/source=chat block.
    frontmatter: VaultFrontmatter | None = None


class EditRequest(BaseModel):
    """Replace one exact passage of a note's body."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    old: str = Field(min_length=1)
    new: str = ""
    replace_all: bool = False
    # `old` is a unique fragment of one line; that whole line becomes `new`
    # (or is deleted when `new` is empty).
    whole_line: bool = False
    # sha256 from the last read; a mismatch means the note changed since.
    expect_sha: str | None = Field(default=None, min_length=64, max_length=64)


class SectionRequest(BaseModel):
    """Replace the body under one heading, or add the section."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    heading: str = Field(min_length=1, max_length=200)
    content: str = ""
    # `append` adds `content` on the line after the section's last line.
    mode: Literal["replace", "append"] = "replace"
    level: int | None = Field(default=None, ge=1, le=6)
    create: bool = True
    expect_sha: str | None = Field(default=None, min_length=64, max_length=64)


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="Edit in Obsidian", min_length=1, max_length=200)


class HistoryEntry(BaseModel):
    commit: str
    author: str
    date: str
    message: str


class HistoryResult(BaseModel):
    path: str | None
    entries: list[HistoryEntry]


class CanvasContent(BaseModel):
    path: str
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    sha: str


class CanvasWriteRequest(BaseModel):
    """Create or replace a canvas.

    Canvases are written whole: an agent reads, edits and writes the document
    back. There is no append, because node geometry means a partial write has
    no sensible meaning.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class AppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    heading: str = Field(min_length=1, max_length=200)
    content: str = ""


class FrontmatterPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    patch: dict[str, Any] = Field(default_factory=dict)


class TrashRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    reason: str | None = Field(default=None, max_length=500)


class MapPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: dict[str, Any] = Field(default_factory=dict)


WriteResultKind = Literal["created", "updated", "trashed", "patched", "appended", "recorded"]


class WriteResult(BaseModel):
    kind: WriteResultKind
    path: str
    sha: str | None = None
    detail: str | None = None
