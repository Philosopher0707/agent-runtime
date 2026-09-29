# 0037 — The durable knowledge is tracked, not scratch

Date: 2026-09-29
Status: accepted

## Context

`.workbuddy-ai/memory/MEMORY.md` was the durable knowledge for this project: the conventions the code
does not show you, and the traps that have already been paid for. It was also **gitignored**
(`.gitignore:27`), and that was deliberate — agent working memory is workspace-local scratch, and the
tracked record was [`AGENTS_LEARNING.md`](../../AGENTS_LEARNING.md).

That call was right about *scratch* and wrong about *this*. By the end the file held 13,642 bytes of
rules a reader of this repository needs: which invariant the budget rests on, why redaction and replay
are mutually exclusive, that `cp` is aliased in this shell, that `-o addopts=` breaks an execution run.
**None of it was in the repository.** Anyone cloning the project got the spec, the decisions and the
history — and none of the operational rules.

So the knowledge had become project source that happened to live in a gitignored directory, and the
symptom was a budget: the file had a ~3,000-character write limit per session, it had grown past
13,000, and distilling it — done once, 24% smaller — only resets the clock. The content is *supposed*
to accumulate. A rule that says "keep this under 5 KB" applied to a document whose purpose is to
collect everything is a rule that guarantees a periodic rewrite of the truth.

## Decision

The durable knowledge moves to [`REFERENCE.md`](../../REFERENCE.md) at the repository root, tracked.
`MEMORY.md` shrinks to the pointer the session loader injects, plus one sentence.

**Move, not copy.** A second copy of a rule is the defect this project keeps finding — a `.env.example`
advertising six variables nothing read, a roadmap count, a decision citing a test that was never
written. Two files holding the same rule would drift, and the copy that drifts is always the one nobody
is reading. So the memory file states the *precedence* instead of restating the rules: **where the two
disagree, `REFERENCE.md` wins.** The one sentence it keeps is labelled as a summary for priming rather
than as the authority.

## What was built

- `REFERENCE.md` — the whole of it, with decision ids as real links so the existing link check covers
  them rather than leaving them as bare backticked numbers.
- `MEMORY.md` — **13,642 → 1,423 bytes**: the pointer, the one sentence everything else follows from,
  and a rule for its own growth ("it holds only what must be known before `REFERENCE.md` is read").
- `scripts/derive_notes.py` — a row, because `NOTES.md` refuses to emit a page that misses a document.
  Group 1 becomes "The root documents", which is what it always was: `REGISTER.md` is not an
  instruction file.
- `tests/test_repo_hygiene.py` — `REFERENCE.md` joins `citing_docs()`.

## Why it is in the citation-checked set

A root file sits outside `docs/`, so it was not in `citing_docs()` by default — and `REFERENCE.md`
cites more tests than any other document, because nearly every rule in it names the test that pins the
rule. A file that cites tests as evidence is exactly what that check is for, and leaving it out would
have made the new authority the one document whose citations could rot unnoticed.

The three exclusions still hold and are different from each other. `AGENTS_LEARNING.md` is out because
it must be free to name things that were wrong. `REGISTER.md` and `NOTES.md` are out because they are
generated and derived from the things they name. `REFERENCE.md` is neither, so it is in.

## What this does not do

- **It does not add a guard for `REFERENCE.md`'s prose.** Its citations are checked; its rules are
  judgement, and a test that pinned them would be pinning prose. The register and the notes page are
  guarded because their contents are *derivable* — this one is not.
- **It does not move `AGENTS_LEARNING.md`.** That is the dated history, it stays where it is, and
  nothing about it changed.
- **It does not delete the memory file.** It still holds the one sentence and it still points; a
  session that reads nothing else still learns what the one rule is and where to look.

## Consequences

**`AGENTS.md` needed room for a fourth time.** It had 27 bytes of headroom and the pointer needed
about 30, so something had to go — and the something was a stale line, not a sacrifice:
`Baseline at initialisation: 0 tests, empty eval set.` That was true when the repository was
initialised and is actively misleading now that the suite is 597 cases. The actionable half of the
sentence — *never quote a count from memory* — is the half that was kept. The root-documents bullet
also absorbed `REFERENCE.md` in place of gaining a bullet of its own, and lost the word "Generated",
which would now be wrong: `REFERENCE.md` is hand-written.

The pattern is worth stating, because it has now happened four times: **the byte budget has never once
cost a rule.** Each trim has removed something that was duplicated or stale, and the guard has never
been the thing sacrificed. A budget that fails loudly is what produces that.
