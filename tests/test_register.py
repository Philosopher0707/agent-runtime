"""The register guard: `REGISTER.md` is a projection of the suite, not a copy of it.

A page that restates a number is a second copy of the truth, and this project has watched a second
copy drift every time it has made one — a roadmap count, a `.env.example` promising six variables
nothing read, a cited test that had never been written. So the register is checked from **two
independent directions**:

* the page against its generator — a fresh render must equal the committed one, normalising only
  what is a property of *when*; and
* the page against the **filesystem and a real collection** — every test file present exactly once,
  every count a measurement.

Both, because either alone is satisfiable by a generator and a page that are wrong in the same way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import register as reg

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = REPO_ROOT / "REGISTER.md"

#: A row of a category table: `| 1.2 | `tests/test_config.py` | 33 | ... |`
ROW = re.compile(r"^\| (\d+\.\d+) \| `tests/(test_[a-z_0-9]+\.py)` \| (\d+) \|", re.MULTILINE)
#: A row of the summary table: `| 1 | Boundary | 5 | 116 | ... |`
SUMMARY = re.compile(r"^\| (\d+) \| ([^|]+?) \| (\d+) \| (\d+) \|", re.MULTILINE)
TOTALS = re.compile(r"^\*\*(\d+) files · (\d+) cases\.\*\*$", re.MULTILINE)


def page_text() -> str:
    assert PAGE.is_file(), (
        "REGISTER.md is missing. It is the map of the suite; generate it with `make register`."
    )
    return PAGE.read_text(encoding="utf-8")


def committed_rows() -> dict[str, int]:
    """The page's rows, read as text rather than through the generator that wrote them."""
    return {name: int(count) for _, name, count in ROW.findall(page_text())}


# ------------------------------------------------------------------ the page exists


def test_the_page_declares_itself_generated() -> None:
    """A page a reader might edit is a page that will be edited, and then it is a copy."""
    text = page_text()
    assert reg.BANNER in text
    assert "tests/register.py" in text


def test_the_register_is_discoverable() -> None:
    """A register nobody knows to read is a register that rots. The README is the entry point."""
    assert "REGISTER.md" in (REPO_ROOT / "README.md").read_text(encoding="utf-8")


# ------------------------------------------------------- checked against the filesystem


def test_every_test_file_appears_exactly_once() -> None:
    rows = ROW.findall(page_text())
    named = [name for _, name, _ in rows]
    duplicated = sorted({name for name in named if named.count(name) > 1})
    assert not duplicated, f"these files are listed twice: {duplicated}"

    on_disk = reg.test_files_on_disk()
    assert set(named) == on_disk, (
        f"the page and the suite disagree. On disk but not listed: "
        f"{sorted(on_disk - set(named))}. Listed but not on disk: {sorted(set(named) - on_disk)}. "
        f"A test file with no row is a failure nobody can read."
    )


def test_every_count_is_a_measurement() -> None:
    """The AGENTS.md rule — never quote a count from memory, run it — made mechanical."""
    measured = reg.collect().files
    listed = committed_rows()
    wrong = {
        name: (listed[name], measured[name])
        for name in listed
        if measured.get(name) != listed[name]
    }
    assert not wrong, (
        f"the page's counts are stale (listed, measured): {wrong}. Run `make register` and commit "
        f"the result — a count that is not re-measured is a count that will be wrong."
    )


def test_the_totals_are_the_sum_of_the_rows() -> None:
    """A headline number that does not add up is worse than no headline: it is quoted."""
    match = TOTALS.search(page_text())
    assert match, "the page no longer states its own totals"
    files, cases = int(match.group(1)), int(match.group(2))
    rows = committed_rows()
    assert files == len(rows), f"the page claims {files} files and lists {len(rows)}"
    assert cases == sum(rows.values()), (
        f"the page claims {cases} cases and its rows sum to {sum(rows.values())}"
    )
    # And the headline is pytest's own total, not the sum of a table we also wrote. Two
    # measurements, so a page that lost a row cannot agree with itself.
    assert cases == reg.collect().total, (
        f"the page claims {cases} cases and pytest collected {reg.collect().total}"
    )


def test_the_boundary_comes_first() -> None:
    """The diagnostic order, asserted rather than assumed: a broken seam explains everything
    below it, so category 1 is where a reader looks first."""
    numbers = [int(number) for number, _, _, _ in SUMMARY.findall(page_text())]
    assert numbers == list(range(1, len(reg.CATEGORIES) + 1)), (
        f"the categories are numbered {numbers}; they must be 1..{len(reg.CATEGORIES)} in order"
    )
    assert reg.CATEGORIES[0].name == "Boundary", "the boundary is no longer the first category"
    assert "| 1 | Boundary |" in page_text()


def test_every_category_has_rows() -> None:
    """A category with no tests is a heading, and a failure can never be pinpointed to it."""
    listed = committed_rows()
    for category in reg.CATEGORIES:
        present = [file for file in category.files if file in listed]
        assert present, f"category {category.number} ({category.name}) lists no rows"


# ------------------------------------------------------------ checked against the generator


def test_the_page_matches_a_fresh_render() -> None:
    """Reproducibility is what makes "regenerate, do not edit" true rather than aspirational.

    Normalising only the `_Taken …_` line, and normalising it on **both** sides — a value that
    appears in two places and is normalised in one of them fails a page that is correct.
    """
    fresh = reg.render(reg.build())
    assert reg.normalise(page_text()) == reg.normalise(fresh), (
        "REGISTER.md is not what its generator produces. Run `make register`."
    )


def test_every_description_is_cited_from_its_file() -> None:
    """The descriptions are the files' own docstrings. A paraphrase is a second copy of the
    truth, and the register exists because second copies drift."""
    for row in ROW.findall(page_text()):
        _, name, _ = row
        assert reg.cited_summary(name) in page_text(), (
            f"the description for {name} is not what the file says about itself — regenerate."
        )


def test_the_summary_table_agrees_with_the_rows() -> None:
    """Two tables in one page are two statements of the same numbers."""
    listed = committed_rows()
    for number, name, files, cases in SUMMARY.findall(page_text()):
        category = next(c for c in reg.CATEGORIES if c.number == int(number))
        assert name.strip() == category.name
        assert int(files) == len(category.files)
        assert int(cases) == sum(listed[file] for file in category.files)


# ---------------------------------------------------------------------- non-vacuity


def test_the_register_is_not_vacuous() -> None:
    """Floors, so a page that lists nothing cannot pass by finding nothing."""
    rows = committed_rows()
    assert len(rows) >= 20, f"only {len(rows)} files registered — the suite has not shrunk this far"
    assert sum(rows.values()) >= 400, "the case floor is not met"
    assert len(reg.CATEGORIES) >= 5, "the categories have collapsed"


def test_every_category_file_list_is_covered_by_the_suite() -> None:
    """The generator's own totality rule, asserted here as well: an uncategorised file must be
    a failure, not a silent omission."""
    categorised = [file for category in reg.CATEGORIES for file in category.files]
    assert set(categorised) == reg.test_files_on_disk()


# ----------------------------------------------------- the generator refuses, not emits


def test_a_cell_containing_a_pipe_is_refused() -> None:
    """The defect this refusal exists for: a `|` silently splits the markdown row, so the page
    looks complete while being short — and the guard, reading the page, cannot see the loss."""
    with pytest.raises(reg.RegisterError, match="splits the markdown row"):
        reg._cell("a | b")


def test_an_uncategorised_test_file_is_refused() -> None:
    """Totality is mechanical: it is the *filesystem* that decides what must have a row."""
    assert set(reg.test_files_on_disk()) == {
        file for category in reg.CATEGORIES for file in category.files
    }, "a test file exists with no category — `build()` would refuse to emit"


def test_a_file_with_no_docstring_cannot_be_cited() -> None:
    with pytest.raises(reg.RegisterError, match="does not exist"):
        reg.cited_summary("test_this_file_does_not_exist.py")


def test_the_when_line_is_the_only_thing_normalised() -> None:
    """The guard normalises exactly one line. If a second value ever became a property of *when*,
    this would start failing on a page that is correct — which is the failure mode the
    normalisation exists to avoid."""
    page = page_text()
    assert len(reg.TAKEN.findall(page)) == 1
    assert reg.normalise(page).count("_Taken") == 1


# ------------------------------------------------------ provenance that cannot dangle

#: A token that could be a short SHA: 7 to 40 hex characters, containing at least one digit
#: **and** one letter. Both conditions matter, and both are there to stop the check firing on
#: prose — a pure number is not a revision, and a hex-looking English word (`acceded`) is not one.
REVISION_LIKE = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")


def test_the_page_records_no_revision() -> None:
    """The defect this pins was created by a rebase merge, not by a mistake.

    The page used to name the revision it was generated at, and it read as provenance — until the
    branch carrying it was rebase-merged. A rebase rewrites the commit, so the file then pointed at
    a SHA that is not in the history: a dangling reference, inside a file whose entire purpose is
    not lying about what is true.

    The date says *when*. `git log REGISTER.md` says which commit touched it, and git is the
    authority on that rather than a copy of it.
    """
    found = REVISION_LIKE.findall(page_text())
    assert not found, (
        f"REGISTER.md names {found}, which look like revisions. A SHA inside a file is stale by "
        f"construction under a rebase merge — remove it from the generator, not from the page."
    )


def test_the_revision_check_is_not_vacuous() -> None:
    """It must match a revision and must not match the date, or it is either blind or noisy."""
    assert REVISION_LIKE.search("_Taken 2026-09-29 22:39 IST, at `4c70732`._")
    assert REVISION_LIKE.search("at `318656b-dirty`")
    assert not REVISION_LIKE.search("_Taken 2026-09-29 22:39 IST._")
    assert not REVISION_LIKE.search("acceded")
