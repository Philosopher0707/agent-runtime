"""Repo hygiene: the files that instruct future agents must stay usable.

`AGENTS.md` says its loader "cuts the tail" past 8,000 bytes. Silent truncation of
instructions is the failure mode — an agent would read a spec that stops mid-sentence and
have no way to know. The budget is therefore checked rather than trusted.

The same reasoning applies to `AGENTS_LEARNING.md`: an obligation to maintain a file is
only durable if it is discoverable from the file every agent reads first, and if the file's
own format is enforced rather than remembered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"
README_MD = REPO_ROOT / "README.md"
LEARNING_LOG = REPO_ROOT / "AGENTS_LEARNING.md"

#: The budget AGENTS.md states for itself.
SIZE_BUDGET_BYTES = 8_000

DATE_HEADING = re.compile(r"^### \d{4}-\d{2}-\d{2}\b", re.MULTILINE)


def test_agents_md_stays_within_its_own_size_budget() -> None:
    size = len(AGENTS_MD.read_bytes())
    assert size <= SIZE_BUDGET_BYTES, (
        f"AGENTS.md is {size} bytes, over its own {SIZE_BUDGET_BYTES}-byte budget by "
        f"{size - SIZE_BUDGET_BYTES}. The loader cuts the tail, so the end of the file is "
        f"now silently missing. Move detail into docs/ and leave a pointer."
    )


def test_agents_md_points_at_the_learning_log() -> None:
    """The obligation has to be discoverable from the file every agent reads first."""
    assert "AGENTS_LEARNING.md" in AGENTS_MD.read_text(encoding="utf-8"), (
        "AGENTS.md no longer points at AGENTS_LEARNING.md. An agent that never learns the "
        "log exists will never append to it."
    )


def test_readme_points_at_the_learning_log() -> None:
    assert "AGENTS_LEARNING.md" in README_MD.read_text(encoding="utf-8")


def test_the_learning_log_exists_and_declares_its_sections() -> None:
    text = LEARNING_LOG.read_text(encoding="utf-8")
    assert "## Open questions" in text
    assert "## Log" in text
    assert "## How to maintain this file" in text


def test_the_learning_log_has_entries() -> None:
    text = LEARNING_LOG.read_text(encoding="utf-8")
    assert DATE_HEADING.search(text), "the log has no dated entry"


def test_every_log_entry_is_dated() -> None:
    """Entries are `### YYYY-MM-DD — title`. An undated entry cannot be referenced, and
    the log is meant to be referenced (a later entry may supersede an earlier one)."""
    headings = re.findall(r"^### .+$", LEARNING_LOG.read_text(encoding="utf-8"), re.MULTILINE)
    assert headings, "the log has no entries"
    undated = [heading for heading in headings if not DATE_HEADING.match(heading)]
    assert not undated, f"log entries must start with a date: {undated}"


@pytest.mark.parametrize("path", [AGENTS_MD, LEARNING_LOG, README_MD])
def test_instruction_files_are_not_empty(path: Path) -> None:
    assert path.read_text(encoding="utf-8").strip()


# ------------------------------------------------------------------- the docs tree

LINK = re.compile(r"\]\(([^)#\s]+\.md)\)")


def markdown_files() -> list[Path]:
    return sorted([*REPO_ROOT.glob("*.md"), *(REPO_ROOT / "docs").rglob("*.md")])


def test_the_docs_tree_is_found() -> None:
    """A guard on the guard: if the search stops finding files, the link check proves nothing."""
    assert len(markdown_files()) >= 15


def test_internal_markdown_links_resolve() -> None:
    """A docs tree is only as good as its links, and a moved file orphans them silently.

    Cross-references are how the decision record stays navigable — `0011` points at `0006`,
    the architecture doc points at the decisions that justify it. A broken one is invisible
    until someone clicks it.
    """
    broken: list[str] = []
    for path in markdown_files():
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(REPO_ROOT)} -> {target}")
    assert not broken, f"broken internal doc links: {broken}"


def test_the_link_check_is_not_vacuous() -> None:
    """The floor is deliberately well below the current count.

    Its job is to catch the *pattern* drifting and silently matching nothing — not to pin a
    number that a doc edit would then fail. There are around 17 internal links today; the
    floor is 10.
    """
    total = sum(len(LINK.findall(path.read_text(encoding="utf-8"))) for path in markdown_files())
    assert total >= 10, (
        f"only {total} internal links found across {len(markdown_files())} docs — the pattern "
        f"has probably drifted and the link check is now vacuous"
    )
