"""The guard for `NOTES.md`: the page quotes the documentation, it does not paraphrase it.

A page listing forty documents is forty chances to restate something slightly wrong, and this
project has watched a second copy of the truth drift every time it has made one. So the guard
checks the page from **two directions**, as the register's does:

* against its generator — a fresh render must equal the committed page; and
* against the **filesystem** — every markdown file present exactly once, and every quotation
  actually in the file it cites.

Either alone is satisfiable by a generator and a page that are wrong in the same way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import derive_notes as notes

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "NOTES.md"

#: `| 1.2 | `docs/roadmap.md` | the claim |`
ROW = re.compile(r"^\| (\d+\.\d+) \| `([^`]+)` \| (.+) \|$", re.MULTILINE)


def page_text() -> str:
    assert PAGE.is_file(), "NOTES.md is missing. Generate it with `make notes`."
    return PAGE.read_text(encoding="utf-8")


def rows() -> list[tuple[str, str, str]]:
    """The page's rows, read as text rather than through the generator that wrote them."""
    return ROW.findall(page_text())


def normalise(text: str) -> str:
    """Whitespace-collapsed, because the documents are hard-wrapped and a quote spans lines."""
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------- the page exists


def test_the_page_declares_itself_generated() -> None:
    text = page_text()
    assert notes.BANNER in text
    assert "scripts/derive_notes.py" in text


def test_the_page_is_discoverable() -> None:
    """A page nobody knows to read is a page that rots."""
    assert "NOTES.md" in (REPO_ROOT / "README.md").read_text(encoding="utf-8")


# ------------------------------------------------------ checked against the filesystem


def test_every_markdown_file_appears_exactly_once() -> None:
    named = [path for _, path, _ in rows()]
    duplicated = sorted({p for p in named if named.count(p) > 1})
    assert not duplicated, f"these files are listed twice: {duplicated}"

    on_disk = set(notes.documents())
    assert set(named) == on_disk, (
        f"the page and the documentation disagree. On disk but not listed: "
        f"{sorted(on_disk - set(named))}. Listed but not on disk: {sorted(set(named) - on_disk)}. "
        f"A document with no row is one nobody will distil."
    )


def test_every_quotation_is_verbatim() -> None:
    """The whole point. A row that is not in the file is a paraphrase that has become what the
    document 'says' — and the reader has no way to tell, because the quotation marks look right."""
    wrong: list[str] = []
    for _, path, point in rows():
        source = normalise((REPO_ROOT / path).read_text(encoding="utf-8"))
        if normalise(point) not in source:
            wrong.append(f"{path}: {point[:70]!r}")
    assert not wrong, (
        f"these rows quote something their file does not say: {wrong}. Either the document changed "
        f"or the row was paraphrased — both are fixed in the generator, not on the page."
    )


def test_a_decision_quotes_its_own_heading() -> None:
    """A decision's row is its heading minus the number, so there is nothing to reword. This asserts
    the row is not merely *in* the file but is the claim the file leads with."""
    for _, path, point in rows():
        if not path.startswith("docs/decisions/"):
            continue
        heading = notes.DECISION_HEADING.search((REPO_ROOT / path).read_text(encoding="utf-8"))
        assert heading, f"{path} has no `# NNNN — claim` heading"
        assert point == heading.group(2).strip(), (
            f"{path} is listed as {point!r}, but its heading says {heading.group(2).strip()!r}"
        )


def test_every_group_has_rows() -> None:
    listed = {path for _, path, _ in rows()}
    for group in notes.GROUPS:
        if group.files:
            assert [f for f in group.files if f in listed], f"group {group.number} lists no rows"
        else:
            assert notes.decisions(), f"group {group.number} ({group.name}) is empty"


# ------------------------------------------------------------ checked against the generator


def test_the_page_matches_a_fresh_render() -> None:
    """Reproducibility is what makes "regenerate, do not edit" true rather than aspirational. Unlike
    the register there is nothing to normalise: this page has no property of *when*."""
    assert page_text() == notes.render(notes.build()), (
        "NOTES.md is not what its generator produces. Run `make notes`."
    )


# ---------------------------------------------------------------------- non-vacuity


def test_the_page_is_not_vacuous() -> None:
    assert len(rows()) >= 30, f"only {len(rows())} documents listed — the docs tree is bigger"
    assert len(notes.decisions()) >= 25, "the decision record has shrunk unexpectedly"
    assert len(notes.LEAD_CLAIMS) >= 7, "the guides have lost their lead claims"


def test_the_quotation_check_would_catch_a_paraphrase() -> None:
    """The guard on the guard: a rewording must not be found in the file."""
    source = normalise((REPO_ROOT / "docs/roadmap.md").read_text(encoding="utf-8"))
    assert normalise(notes.LEAD_CLAIMS["docs/roadmap.md"]) in source
    assert "Where this is headed, in dependency order." not in source


# ----------------------------------------------------- the generator refuses, not emits


def test_a_cell_containing_a_pipe_is_refused() -> None:
    with pytest.raises(notes.NotesError, match="splits the markdown row"):
        notes._cell("a | b")


def test_a_document_with_no_row_is_refused() -> None:
    """Totality is mechanical: it is the *filesystem* that decides what must have a row."""
    listed = {f for group in notes.GROUPS for f in group.files} | set(notes.decisions())
    assert listed == set(notes.documents()), (
        "a markdown file exists with no row — `build()` would refuse to emit"
    )
