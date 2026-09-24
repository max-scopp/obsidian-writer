"""Tests for in-place edits: sections, exact passages, and the `updated` stamp."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from obsidian_writer.edit import (
    AmbiguousError,
    NotFoundError,
    replace_line,
    replace_section,
    replace_text,
    split_note,
    touch,
)

NOTE = """---
type: core
scope: shared
topics: [preferences]
updated: 2026-09-22
source: reconstruction
---

# Preferences

Intro line.

## Communication

- Direct answers.

## Development

- TypeScript.

### Tooling

- pnpm.

## Infrastructure

- Self-hosted.
"""


def test_split_note_keeps_the_frontmatter_block_verbatim() -> None:
    block, body = split_note(NOTE)
    assert block.startswith("---\ntype: core\n")
    assert block.endswith("---\n")
    assert body.startswith("\n# Preferences")
    assert block + body == NOTE


def test_split_note_without_frontmatter() -> None:
    assert split_note("# Plain\n") == ("", "# Plain\n")


def test_touch_sets_updated_and_keeps_formatting() -> None:
    block, _ = split_note(NOTE)
    now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
    touched = touch(block, now=now)
    assert "updated: 2026-09-23\n" in touched
    # Flow-style lists and key order survive untouched.
    assert "topics: [preferences]\n" in touched
    assert touched.index("type:") < touched.index("scope:") < touched.index("updated:")


def test_touch_refreshes_legacy_modified_without_adding_updated() -> None:
    block = "---\ntitle: Old\nmodified: 2026-01-01T00:00:00Z\n---\n"
    touched = touch(block, now=datetime(2026, 9, 23, 8, 0, tzinfo=UTC))
    assert "modified: 2026-09-23T08:00:00Z" in touched
    assert "updated:" not in touched


def test_touch_adds_updated_when_the_block_has_no_date() -> None:
    touched = touch("---\ntype: memory\n---\n", now=datetime(2026, 9, 23, tzinfo=UTC))
    assert touched == "---\ntype: memory\nupdated: 2026-09-23\n---\n"


def test_touch_leaves_a_note_without_frontmatter_alone() -> None:
    assert touch("") == ""


def test_replace_section_rewrites_only_that_section() -> None:
    _, body = split_note(NOTE)
    new_body, created = replace_section(body, "communication", "- Terse.\n- No preamble.")
    assert not created
    assert "- Terse.\n- No preamble.\n\n## Development" in new_body
    assert "Direct answers" not in new_body
    assert "- TypeScript." in new_body and "- Self-hosted." in new_body


def test_replace_section_includes_its_subsections() -> None:
    _, body = split_note(NOTE)
    new_body, _ = replace_section(body, "Development", "- Rust too.")
    assert "### Tooling" not in new_body
    assert "- Rust too.\n\n## Infrastructure" in new_body


def test_replace_section_at_the_end_of_the_note() -> None:
    _, body = split_note(NOTE)
    new_body, _ = replace_section(body, "Infrastructure", "- Local-first.")
    assert new_body.endswith("## Infrastructure\n\n- Local-first.\n")


def test_replace_section_adds_a_missing_section() -> None:
    _, body = split_note(NOTE)
    new_body, created = replace_section(body, "Food", "- Spicy.")
    assert created
    assert new_body.endswith("- Self-hosted.\n\n## Food\n\n- Spicy.\n")


def test_replace_section_refuses_to_create_when_asked_not_to() -> None:
    with pytest.raises(NotFoundError):
        replace_section("# T\n", "Missing", "x", create=False)


def test_replace_section_refuses_an_ambiguous_heading() -> None:
    body = "## Notes\n\na\n\n### Notes\n\nb\n"
    with pytest.raises(AmbiguousError):
        replace_section(body, "Notes", "x")
    new_body, _ = replace_section(body, "Notes", "x", level=3)
    assert new_body == "## Notes\n\na\n\n### Notes\n\nx\n"


def test_replace_section_ignores_headings_inside_code_fences() -> None:
    body = "## Script\n\n```sh\n# Script\necho hi\n```\n\n## After\n\nkeep\n"
    new_body, _ = replace_section(body, "Script", "gone")
    assert new_body == "## Script\n\ngone\n\n## After\n\nkeep\n"


def test_replace_text_changes_one_exact_passage() -> None:
    new_body, count = replace_text("- pnpm.\n- npm.\n", "- npm.", "- bun.")
    assert (new_body, count) == ("- pnpm.\n- bun.\n", 1)


def test_replace_text_refuses_a_missing_passage() -> None:
    with pytest.raises(NotFoundError):
        replace_text("abc", "xyz", "q")


def test_replace_text_refuses_an_ambiguous_passage_unless_told() -> None:
    with pytest.raises(AmbiguousError, match="2 times"):
        replace_text("a a", "a", "b")
    assert replace_text("a a", "a", "b", replace_all=True) == ("b b", 2)


def test_append_adds_lines_to_the_end_of_a_section() -> None:
    body = "## Proposed\n\n- [ ] a\n\n## Rejected\n\n- [-] z\n"
    new_body, created = replace_section(body, "Proposed", "- [ ] b", append=True)
    assert not created
    assert new_body == "## Proposed\n\n- [ ] a\n- [ ] b\n\n## Rejected\n\n- [-] z\n"


def test_append_to_an_empty_or_missing_section() -> None:
    new_body, _ = replace_section("## Proposed\n", "Proposed", "- [ ] a", append=True)
    assert new_body == "## Proposed\n\n- [ ] a\n"
    new_body, created = replace_section("# T\n", "Proposed", "- [ ] a", append=True)
    assert created and new_body == "# T\n\n## Proposed\n\n- [ ] a\n"


def test_deleting_a_whole_line_takes_its_line_break() -> None:
    body = "## Proposed\n\n- [ ] a\n- [x] b\n- [ ] c\n"
    assert replace_text(body, "- [x] b", "") == ("## Proposed\n\n- [ ] a\n- [ ] c\n", 1)
    # Part of a line stays part of a line.
    assert replace_text("- [ ] a b\n", " b", "") == ("- [ ] a\n", 1)


REVIEW = (
    "## Proposed\n\n"
    "- [x] **Prüm** — lives in the “Eifel” → [[Core/Identity]] · identity ^lh-mem-a1\n"
    "- [ ] **Tesla** — googled the stock → [[Journal/2026/2026-09-22]] ^lh-mem-b2\n"
)


def test_replace_line_deletes_the_line_a_fragment_names() -> None:
    new_body, deleted = replace_line(REVIEW, "^lh-mem-a1", "")
    assert deleted
    assert new_body == "## Proposed\n\n" + REVIEW.splitlines(keepends=True)[3]


def test_replace_line_rewrites_the_whole_line() -> None:
    new_body, deleted = replace_line(REVIEW, "^lh-mem-b2", "- [-] Tesla")
    assert not deleted
    assert new_body.endswith("^lh-mem-a1\n- [-] Tesla\n")


def test_replace_line_refuses_a_fragment_on_two_lines_or_none() -> None:
    with pytest.raises(AmbiguousError, match="2 lines"):
        replace_line(REVIEW, "^lh-mem-", "")
    with pytest.raises(NotFoundError):
        replace_line(REVIEW, "^lh-mem-zz", "")
    with pytest.raises(NotFoundError):
        replace_line(REVIEW, "a\nb", "")


def test_append_starts_a_new_paragraph_after_prose() -> None:
    body = "## Routing\n\nOllama at port 11434.\n\n## Next\n"
    new_body, _ = replace_section(body, "Routing", "Vault tasks run on agent/vault.", append=True)
    assert new_body == (
        "## Routing\n\nOllama at port 11434.\n\nVault tasks run on agent/vault.\n\n## Next\n"
    )
