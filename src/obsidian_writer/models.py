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


WriteResultKind = Literal["created", "updated", "trashed", "patched", "appended"]


class WriteResult(BaseModel):
    kind: WriteResultKind
    path: str
    sha: str | None = None
    detail: str | None = None
