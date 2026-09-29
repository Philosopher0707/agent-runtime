# 0032 — The register is a projection

Date: 2026-09-29
Status: accepted

## Context

The suite is 553 cases across 28 files, and it is the project's only authority on whether a change
helped. What it did not have was an answer to *"where do I look when it goes red?"* — a failure
reports one test name, and the reader has to reconstruct from that whether the seam moved, the loop
misbehaved, or a capability broke.

The second problem is this project's most-repeated lesson. A number written into a document is a
**second copy of the truth**, and every second copy here has drifted: a roadmap count, a
`.env.example` advertising six variables nothing read, a decision citing a test that had never been
written. `AGENTS.md` states the rule plainly — *never quote a count from memory, run it* — and a
rule that depends on remembering to follow it is the kind of rule that fails silently.

## What was built

`REGISTER.md` — every test file, numbered and grouped by category, with its case count and the
first line of its own docstring — plus the two things that keep it honest:

| file | what it is |
|---|---|
| `tests/register.py` | the committed generator: the categories, the collection, the refusals |
| `REGISTER.md` | the page — generated, never edited |
| `tests/test_register.py` | the guard: sixteen tests, in two independent directions |
| `make register` | regenerate and commit |

## The rules, stated once

1. **The boundary comes first.** Category 1 is the seams other components depend on. A broken seam
   explains every failure below it, so the first row that moves is the one to read — and a run of
   failures inside one category is one defect, not many.
2. **Numbers are measured, never remembered.** The counts come from a real `pytest --collect-only`,
   and the guard re-measures them. The headline is pytest's own reported total, which must equal
   the sum of the rows — two measurements, so a page that lost a row cannot agree with itself.
3. **Descriptions are cited, not written.** Each row's description is the first line of the file's
   module docstring, so the page cannot paraphrase a test into something it is not.
4. **Totality is mechanical.** Every `tests/test_*.py` on disk must appear exactly once. Adding a
   test file without giving it a category fails the suite — a decision a reviewer sees, rather than
   a silent omission.
5. **Exactly one line is a property of *when*.** The `_Taken …_` line is normalised on both sides
   of the reproducibility check; everything else must match byte for byte. A second unchecked line
   would be a second thing that could be wrong without anyone noticing.
6. **The generator refuses rather than emitting.** A `|` inside a cell splits the markdown row, and
   the page then *looks* complete while being short — the guard, reading the page, cannot see what
   is no longer in it. So `_cell` raises, and so does a file with no docstring to cite.

## Why two directions

The guard checks the page against its generator **and** against the filesystem and a real
collection. Either alone is satisfiable by a generator and a page that are wrong in the same way:
a page rendering a count it invented would match its own generator perfectly.

## Evidence

Nine mutations, each breaking one property, each restored from a byte copy with the hash verified:

| mutation | caught by |
|---|---|
| a row deleted | `test_every_test_file_appears_exactly_once` |
| a count changed | `test_every_count_is_a_measurement` |
| the banner removed | `test_the_page_declares_itself_generated` |
| the totals line changed | `test_the_totals_are_the_sum_of_the_rows` |
| the when-line renamed | `test_the_page_matches_a_fresh_render` |
| the boundary renamed | `test_the_boundary_comes_first` |
| descriptions truncated differently | `test_every_description_is_cited_from_its_file` |
| a new test file, no category | `test_every_test_file_appears_exactly_once` |
| a `\|` inside a cited cell | the generator refuses, exit 2 |

**The probe harness lied twice before it told the truth**, and both are the reason the results are
worth anything. It was *blind* first: it invoked pytest with `-q` on top of the `-q` in
`pyproject.toml`, so `-qq` suppressed the summary it was parsing, and every mutation came back
`MISSED` — which looks exactly like a guard that catches everything. Then it was *contaminated*: a
leftover probe file from a killed run made the same tests fail for an unrelated reason, so it
reported `CAUGHT` for mutations it had not isolated. It now runs a self-test whose expected failure
set is asserted exactly, and cleans its own strays before and after.

## A check that was matching itself

`tests/test_env_file.py` searched every file for the literal `'__name__ == "__main__"'` and
therefore matched **its own source**, counting itself as an entry point. It passed only because the
file calls `load_env_file()` in its tests — a check passing by accident. The search is now scoped to
files outside `tests/`, which is the scope the rule always meant: a tool in the test suite starts no
run and reads no key, so demanding it load `.env` demands a call it has no use for.

## Consequences

- **Adding a test file is a two-file change.** That is the point: an uncategorised file is a failure
  nobody knows how to read.
- **The register is not a sixth gate.** It is guarded by a test, so it runs inside `make check` and
  `make ci`'s recipe did not change.
- **The page carries no decision.** Where a category's boundary is genuinely arguable, the register
  records where the file sits; the argument belongs here.
