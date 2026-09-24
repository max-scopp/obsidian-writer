"""In-place edits that leave the rest of the note byte-for-byte intact.

The vault's agent protocol says: update the canonical note, never write a
second one, and correct a wrong fact where it stands. Appending a dated
section does neither. These helpers rewrite exactly one region of a note -
the body under a heading, or one exact passage - and keep everything else,
the frontmatter's own formatting included, as the user left it.

Frontmatter is handled as text here on purpose. Re-rendering it through
`frontmatter.render_note` would reorder keys and restyle lists on every
edit, which turns a one-line correction into a noisy diff.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

_FRONTMATTER = re.compile(r"\A---\r?\n.*?^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = ("```", "~~~")


class EditError(Exception):
    """Base error for an edit that cannot be applied as asked."""


class NotFoundError(EditError):
    """The heading or passage the edit targets is not in the note."""


class AmbiguousError(EditError):
    """The target matches more than once, so the edit would be a guess."""


def split_note(text: str) -> tuple[str, str]:
    """Split raw note text into (frontmatter block, body).

    The block keeps its `---` fences and trailing newline; it is empty when
    the note has no frontmatter.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return "", text
    return text[: match.end()], text[match.end() :]


def touch(block: str, *, now: datetime | None = None) -> str:
    """Record an edit in a raw frontmatter block.

    `updated` (the vault's date field) is set to today, and a `modified`
    timestamp - the field notes created by older versions of this service
    carry - is refreshed if present. A block with neither gains `updated`.
    A note without frontmatter is left without it: this service does not
    decide a note's `type`.
    """
    if not block:
        return block
    now = now or datetime.now(UTC)
    lines = block.splitlines(keepends=True)
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    closing = len(lines) - 1
    seen: set[str] = set()
    for i in range(1, closing):
        key = lines[i].split(":", 1)[0].strip()
        if key == "updated":
            lines[i] = f"updated: {now:%Y-%m-%d}{newline}"
            seen.add(key)
        elif key == "modified":
            lines[i] = f"modified: {now:%Y-%m-%dT%H:%M:%SZ}{newline}"
            seen.add(key)
    if not seen:
        lines.insert(closing, f"updated: {now:%Y-%m-%d}{newline}")
    return "".join(lines)


def replace_section(
    body: str,
    heading: str,
    content: str,
    *,
    level: int | None = None,
    create: bool = True,
    append: bool = False,
) -> tuple[str, bool]:
    """Replace the body under `heading`, or add the section if it is missing.

    A section runs from its heading line to the next heading of the same or
    a higher level, so its subsections are part of it and are replaced too.
    Headings match case-insensitively with whitespace collapsed; `level`
    narrows the match to one heading depth. Fenced code blocks are skipped,
    so a `#` comment inside one is never mistaken for a heading.

    With `append`, `content` is added at the end of the section instead of
    replacing it. That is the safe way for several writers to add lines to
    one list: nobody has to resend - and so nobody can drop - lines another
    writer added in between.

    Returns the new body and whether the section was created.

    Raises:
        AmbiguousError: more than one heading matches.
        NotFoundError: nothing matches and `create` is false.
    """
    lines = body.splitlines(keepends=True)
    target = _normalise(heading)
    matches = [
        (i, depth)
        for i, depth, text in _headings(lines)
        if _normalise(text) == target and (level is None or depth == level)
    ]
    if len(matches) > 1:
        raise AmbiguousError(f"{len(matches)} headings match {heading!r}; pass `level`")

    block = content.strip("\n")
    if not matches:
        if not create:
            raise NotFoundError(f"no heading {heading!r} in the note")
        depth = level or 2
        section = (
            f"{'#' * depth} {heading.strip()}\n\n{block}\n"
            if block
            else (f"{'#' * depth} {heading.strip()}\n")
        )
        stem = body.rstrip()
        return (f"{stem}\n\n{section}" if stem else section), True

    start, depth = matches[0]
    end = next(
        (i for i, d, _ in _headings(lines) if i > start and d <= depth),
        len(lines),
    )
    head = lines[start] if lines[start].endswith("\n") else lines[start] + "\n"
    if append:
        existing = "".join(lines[start + 1 : end]).strip("\n")
        block = _join(existing, block)
    replacement = [head, "\n"]
    if block:
        replacement.append(block + "\n")
    if end < len(lines):
        replacement.append("\n")
    return "".join(lines[:start] + replacement + lines[end:]), False


def replace_text(body: str, old: str, new: str, *, replace_all: bool = False) -> tuple[str, int]:
    """Replace an exact passage of the body.

    `old` must occur exactly once unless `replace_all` is set - an edit that
    could land in two places is refused rather than guessed.

    Returns the new body and the number of replacements made.
    """
    if not old:
        raise NotFoundError("the passage to replace must not be empty")
    if not new and _whole_lines(body, old):
        # Replacing whole lines with nothing deletes them - line breaks included,
        # so removing an item from a list does not leave a gap in it.
        old += "\n"
    count = body.count(old)
    if count == 0:
        raise NotFoundError("the passage is not in the note; read it again before editing")
    if count > 1 and not replace_all:
        raise AmbiguousError(
            f"the passage occurs {count} times; quote more context or set replace_all"
        )
    if replace_all:
        return body.replace(old, new), count
    return body.replace(old, new, 1), 1


def replace_line(body: str, fragment: str, new: str) -> tuple[str, bool]:
    """Replace the one line that contains `fragment`, or delete it.

    The caller quotes only a unique part of the line - a block id such as
    `^lh-mem-abc`, or a few distinctive words - instead of retyping it. A
    model asked to reproduce a long line with typographic quotes, arrows and
    umlauts gets a character wrong often enough that exact matching fails;
    a fragment it copies reliably.

    An empty `new` deletes the line, its line break included. Returns the
    new body and whether the line was deleted.
    """
    if not fragment.strip() or "\n" in fragment:
        raise NotFoundError("quote a fragment of a single line")
    lines = body.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if fragment in line]
    if not hits:
        raise NotFoundError("no line contains that fragment; read the note again")
    if len(hits) > 1:
        raise AmbiguousError(f"{len(hits)} lines contain that fragment; quote more of the line")
    i = hits[0]
    if not new.strip("\r\n"):
        del lines[i]
        return "".join(lines), True
    ending = lines[i][len(lines[i].rstrip("\r\n")) :] or ""
    lines[i] = new.rstrip("\r\n") + ending
    return "".join(lines), False


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def _join(existing: str, addition: str) -> str:
    """Append `addition` after `existing`: a list grows by a line, prose by a paragraph."""
    if not existing or not addition:
        return existing or addition
    last = existing.splitlines()[-1]
    first = addition.splitlines()[0]
    separator = "\n" if _LIST_ITEM.match(last) and _LIST_ITEM.match(first) else "\n\n"
    return existing + separator + addition


def _whole_lines(body: str, passage: str) -> bool:
    """True when every occurrence of `passage` is a complete line of `body`."""
    if passage.endswith("\n") or passage not in body:
        return False
    lines = body.count("\n" + passage + "\n") + body.startswith(passage + "\n")
    return lines == body.count(passage)


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, depth, text) for every heading outside a code fence."""
    out: list[tuple[int, int, str]] = []
    fenced = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(_FENCE):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _HEADING.match(line.rstrip("\r\n"))
        if match:
            out.append((i, len(match.group(1)), match.group(2)))
    return out
