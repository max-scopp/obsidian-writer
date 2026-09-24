"""JSONCanvas documents (`*.canvas`).

Obsidian canvases are plain JSON, not Markdown: no frontmatter, and a
structure Obsidian refuses to open if it is malformed. An agent that writes
a broken canvas produces a file the user can only fix by hand, so every
write goes through `validate` first.

Spec: https://jsoncanvas.org/spec/1.0/ — we accept the 1.0 shape and are
deliberately strict about the parts Obsidian relies on (ids, node types,
geometry, edge endpoints) while passing unknown keys through untouched so a
newer Obsidian version does not lose data on a read/modify/write cycle.
"""

from __future__ import annotations

import json
from typing import Any, cast

NODE_TYPES = frozenset({"text", "file", "link", "group"})
SIDES = frozenset({"top", "right", "bottom", "left"})
ENDS = frozenset({"none", "arrow"})
# Obsidian's palette is "1".."6"; any other string is treated as a hex colour.
_REQUIRED_BY_TYPE = {"text": "text", "file": "file", "link": "url"}


class CanvasError(ValueError):
    """Raised when a canvas document is not something Obsidian can open."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise CanvasError(msg)


def _check_node(node: Any, index: int, seen: set[str]) -> str:
    where = f"nodes[{index}]"
    _require(isinstance(node, dict), f"{where} must be an object")

    node_id = node.get("id")
    _require(
        isinstance(node_id, str) and node_id != "",
        f"{where}.id must be a non-empty string",
    )
    _require(node_id not in seen, f"{where}.id {node_id!r} is duplicated")

    node_type = node.get("type")
    _require(
        node_type in NODE_TYPES,
        f"{where}.type must be one of {sorted(NODE_TYPES)}, got {node_type!r}",
    )

    for axis in ("x", "y", "width", "height"):
        _require(
            isinstance(node.get(axis), (int, float))
            and not isinstance(node.get(axis), bool),
            f"{where}.{axis} must be a number",
        )

    required = _REQUIRED_BY_TYPE.get(str(node_type))
    if required is not None:
        _require(
            isinstance(node.get(required), str),
            f"{where} of type {node_type!r} needs a string {required!r}",
        )

    return cast(str, node_id)


def _check_edge(edge: Any, index: int, node_ids: set[str], seen: set[str]) -> None:
    where = f"edges[{index}]"
    _require(isinstance(edge, dict), f"{where} must be an object")

    edge_id = edge.get("id")
    _require(
        isinstance(edge_id, str) and edge_id != "",
        f"{where}.id must be a non-empty string",
    )
    _require(edge_id not in seen, f"{where}.id {edge_id!r} is duplicated")
    seen.add(edge_id)

    for end in ("fromNode", "toNode"):
        ref = edge.get(end)
        _require(isinstance(ref, str), f"{where}.{end} must be a string")
        # A dangling edge makes Obsidian drop the edge silently, which looks
        # like the write half-succeeded. Fail loudly instead.
        _require(ref in node_ids, f"{where}.{end} points at unknown node {ref!r}")

    for side in ("fromSide", "toSide"):
        if side in edge:
            _require(edge[side] in SIDES, f"{where}.{side} must be one of {sorted(SIDES)}")
    for end in ("fromEnd", "toEnd"):
        if end in edge:
            _require(edge[end] in ENDS, f"{where}.{end} must be one of {sorted(ENDS)}")


def validate(doc: Any) -> dict[str, Any]:
    """Return `doc` unchanged if it is a usable canvas, else raise CanvasError."""
    _require(isinstance(doc, dict), "canvas must be a JSON object")

    nodes = doc.get("nodes", [])
    edges = doc.get("edges", [])
    _require(isinstance(nodes, list), "canvas.nodes must be an array")
    _require(isinstance(edges, list), "canvas.edges must be an array")

    node_ids: set[str] = set()
    for i, node in enumerate(nodes):
        node_ids.add(_check_node(node, i, node_ids))

    edge_ids: set[str] = set()
    for i, edge in enumerate(edges):
        _check_edge(edge, i, node_ids, edge_ids)

    return cast(dict[str, Any], doc)


def loads(text: str) -> dict[str, Any]:
    """Parse canvas JSON from disk, tolerating an empty file as an empty canvas."""
    if text.strip() == "":
        return {"nodes": [], "edges": []}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CanvasError(f"not valid JSON: {exc}") from exc
    return validate(doc)


def dumps(doc: dict[str, Any]) -> str:
    """Serialise a canvas the way Obsidian writes them: 2-space indent, trailing newline."""
    normalised = {"nodes": doc.get("nodes", []), "edges": doc.get("edges", [])}
    for key, value in doc.items():
        if key not in ("nodes", "edges"):
            normalised[key] = value
    return json.dumps(normalised, indent=2, ensure_ascii=False) + "\n"
