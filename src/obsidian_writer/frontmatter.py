"""Frontmatter generation, patching, and rendering.

The writer generates frontmatter server-side on `create` so the agent can
never inject arbitrary YAML keys that look like metadata but aren't
(`source`, `conversation`, `path`). These fields are reserved: the agent
may read them but `patch_frontmatter` rejects attempts to change them.

The agent supplies user-visible fields in a typed struct; we render to
YAML in a single pass, escaping any body content that would break out of
the YAML block.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import frontmatter  # type: ignore[import-untyped]
import yaml

# Fields the agent is forbidden to modify via PATCH.
RESERVED_FRONTMATTER_FIELDS: frozenset[str] = frozenset(
    {"created", "source", "conversation", "path"}
)

# `source` is reserved with one exception. In a vault that follows the
# Athenaeum contract it marks a note as unverified, and the agent protocol
# requires agents to set it on inferred content and drop it once a fact is
# confirmed. So it may take exactly these values, or null to remove it.
UNVERIFIED_SOURCES: frozenset[str] = frozenset({"reconstruction", "inferred"})

# Default key order in the rendered frontmatter (preserves human-readable
# order — Obsidian doesn't care, but `git diff` does).
DEFAULT_FIELD_ORDER: tuple[str, ...] = (
    "type",
    "title",
    "scope",
    "agent",
    "topics",
    "created",
    "modified",
    "updated",
    "source",
    "conversation",
    "model",
    "tags",
    "status",
    "due",
    "aliases",
)


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_note(metadata: dict[str, Any], body: str) -> str:
    """Render a complete note (frontmatter block + body) from metadata + body.

    The body is concatenated after the YAML block; a single trailing newline
    is appended if the body doesn't already end with one.
    """
    fm_lines = ["---"]
    seen: set[str] = set()

    # Render the canonical keys first, in the canonical order, so the file
    # is stable under `git diff`.
    for key in DEFAULT_FIELD_ORDER:
        if key in metadata:
            fm_lines.extend(_render_key(key, metadata[key]))
            seen.add(key)

    # Render any remaining keys alphabetically.
    for key in sorted(metadata.keys()):
        if key in seen:
            continue
        fm_lines.extend(_render_key(key, metadata[key]))

    fm_lines.append("---")
    fm_lines.append("")  # blank line between frontmatter and body
    output = "\n".join(fm_lines) + "\n" + body
    if not output.endswith("\n"):
        output += "\n"
    return output


def _render_key(key: str, value: Any) -> list[str]:
    """Render a single YAML key/value pair as one or more lines."""
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        return [f"{key}:"] + [f"  - {_scalar(item)}" for item in value]
    if isinstance(value, dict):
        rendered = [f"{key}:"]
        for k, v in value.items():
            rendered.append(f"  {k}: {_scalar(v)}")
        return rendered
    return [f"{key}: {_scalar(value)}"]


def _scalar(value: Any) -> str:
    """Render a YAML scalar with minimal, human-friendly quoting."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "null"
    text = str(value)
    needs_quotes = any(c in text for c in ":#\n\"'`%@&*!|>{}\\[],?")
    if not needs_quotes:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_created(
    title: str,
    *,
    tags: list[str] | None = None,
    conversation: str | None = None,
    model: str | None = None,
    body: str = "",
) -> str:
    """Build a complete note body (frontmatter + body) for a new note.

    Convenience wrapper around `render_note` that supplies the standard
    metadata for a newly created note.
    """
    metadata: dict[str, Any] = {
        "title": title,
        "created": utc_now_iso(),
        "modified": utc_now_iso(),
        "source": "chat",
    }
    if tags:
        metadata["tags"] = sorted(set(t.lower() for t in tags if t))
    if conversation:
        metadata["conversation"] = conversation
    if model:
        metadata["model"] = model
    return render_note(metadata, body)


def render_vault_created(
    title: str,
    fields: dict[str, Any],
    *,
    body: str = "",
) -> str:
    """Build a new note in the vault's own frontmatter contract.

    The shape matches the vault's templates: `type`, `scope`, optional
    `agent`, `topics` as a flow list, optional `status`, `updated` as a date
    and `source` only when the content is unverified. The body gets the
    note's `# Title` heading unless it already opens with one.
    """
    lines = ["---", f"type: {_scalar(fields['type'])}", f"scope: {_scalar(fields['scope'])}"]
    if fields.get("agent"):
        lines.append(f"agent: {_scalar(fields['agent'])}")
    topics = ", ".join(_scalar(t) for t in fields.get("topics") or [])
    lines.append(f"topics: [{topics}]")
    if fields.get("status"):
        lines.append(f"status: {_scalar(fields['status'])}")
    lines.append(f"updated: {datetime.now(UTC):%Y-%m-%d}")
    if fields.get("source"):
        lines.append(f"source: {_scalar(fields['source'])}")
    lines += ["---", ""]
    text = body.lstrip("\n")
    if not text.startswith("# "):
        text = f"# {title}\n\n{text}" if text else f"# {title}\n"
    output = "\n".join(lines) + "\n" + text
    return output if output.endswith("\n") else output + "\n"


def patch_frontmatter_yaml(existing_yaml: str, patch: dict[str, Any]) -> str:
    """Apply a typed patch to existing frontmatter.

    Reserved fields (`created`, `conversation`, `path`) cannot be removed or
    overwritten; attempting to do so raises `ValueError`. `source` may only
    be set to one of `UNVERIFIED_SOURCES`, or to null to remove it.

    Unknown fields are added. Existing fields of compatible type are
    replaced; of incompatible type, raises `TypeError` so the agent
    sees the issue rather than corrupting the note.
    """
    post = frontmatter.loads(existing_yaml)
    metadata: dict[str, Any] = dict(post.metadata)

    for key, value in patch.items():
        if key == "source" and (value is None or value in UNVERIFIED_SOURCES):
            if value is None:
                metadata.pop("source", None)
            else:
                metadata["source"] = value
            continue
        if key == "source":
            raise ValueError(
                "frontmatter field 'source' is reserved: it may only be set to "
                "'reconstruction' or 'inferred', or to null once the note is confirmed"
            )
        if key in RESERVED_FRONTMATTER_FIELDS:
            raise ValueError(f"frontmatter field {key!r} is reserved and cannot be patched")

        if key in metadata:
            current = metadata[key]
            if not _types_compatible(current, value):
                raise TypeError(
                    f"frontmatter field {key!r} has type "
                    f"{type(current).__name__}, patch has {type(value).__name__}"
                )

        metadata[key] = value

    # A vault note (it has a `type` or an `updated` date) records edits in
    # `updated`; notes from before that contract keep their `modified` stamp.
    vault_note = "type" in metadata or "updated" in metadata
    if vault_note:
        metadata["updated"] = datetime.now(UTC).strftime("%Y-%m-%d")
    if "modified" in metadata or not vault_note:
        metadata["modified"] = utc_now_iso()
    return render_note(metadata, post.content)


def _types_compatible(existing: Any, new: Any) -> bool:
    """Return True if `existing` and `new` have YAML-compatible types."""
    if existing is None:
        return True
    if isinstance(existing, bool):
        return isinstance(new, bool)
    if isinstance(existing, (int, float)):
        return isinstance(new, (int, float)) and not isinstance(new, bool)
    if isinstance(existing, str):
        return isinstance(new, str)
    if isinstance(existing, list):
        return isinstance(new, list)
    if isinstance(existing, dict):
        return isinstance(new, dict)
    return False


def parse_frontmatter(path_content: str) -> tuple[dict[str, Any], str]:
    """Split a note into (frontmatter dict, body string).

    Notes without frontmatter return ({}, original). Malformed frontmatter
    raises `ValueError` (from `python-frontmatter`) — the agent should be
    told, not silently repaired.
    """
    post = frontmatter.loads(path_content)
    return dict(post.metadata), post.content


def safe_yaml_dump(data: dict[str, Any]) -> str:
    """Dump a YAML structure for the structure cache (`.obsidian-map.yaml`).

    Stable key order: we iterate the keys in insertion order and dump as
    a block mapping so the file stays diff-friendly under git.
    """
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
