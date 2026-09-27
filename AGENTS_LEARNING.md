# AGENTS_LEARNING.md

What this project has taught us: the surprises, the mistakes, and the questions still
open. Maintained as we go, not reconstructed at the end.

**This is not** a changelog (`git log`), a decision record (`docs/decisions/`), or a
conventions list (`.workbuddy-ai/memory/MEMORY.md`). It is the layer underneath all
three: the reasoning that produced them, including the parts that were wrong first.

## How to maintain this file

- **Append a dated entry when you learn something that would change your next move.**
  Not when you finish a task — when you find out you were wrong, or right for a reason
  you did not expect.
- **Never rewrite an entry.** If a later finding supersedes one, append a new entry that
  says so and names what it supersedes.
- **A learning worth writing cost something**: a bug, a failed test, a wrong assumption,
  a decision reversed. If it cost nothing, it is not a learning.
- **Keep "Open questions" live.** It is the only section that gets edited rather than
  appended. Move a question into the log when it is answered.
- **If a learning hardens into a rule**, it belongs in `AGENTS.md` or a decision file.
  Link to it from here; do not duplicate it.

## Open questions

Things we do not know yet, with how we would settle each one. Ordered by what it would
cost to be wrong.

1. **What is the false-positive rate of the injection markers on real tool output?**
   The guardrail is fail-closed, so a false positive refuses a legitimate run. The marker
   list was written from intuition, not from data. *Settle:* run
   `context.sanitize.detect_injection` over a corpus of real tool results and count hits.
   This is the highest-value unknown, because the cost of being wrong is refusing real
   users.
2. **Does the failure taxonomy hold against a real model?** All 32 eval cases are
   stub-driven. A stub is a *specification of a model*, not a model — it cannot violate
   our assumptions, and real ones can (multi-turn tool use, partial JSON, prose wrapped
   around a tool call). *Settle:* a `provider: openai_compat` variant of the golden set.
3. **Does replay hold for a real provider?** The reasoning says yes: replay returns the
   *recorded* `ModelResponse`, usage included, so accounting is identical, and latency and
   elapsed time are excluded from `RunOutput.canonical()`. The one bound that can differ is
   wall-clock, because a replay is faster than the original. Unverified — no
   real-provider trace has been replayed. *Settle:* run one, replay it, compare.
4. **Is `chars / 4` an adequate token estimate?** It governs when context is summarised or
   dropped, and it is worst for dense non-Latin scripts. *Settle:* compare against a real
   tokeniser on the prompts we actually build.
5. **Is `MAX_ATTEMPTS = 3` right?** Initial + one repair + one retry was derived from the
   taxonomy's wording, not from observed failure rates. *Settle:* measure transient failure
   rates on a real endpoint.
6. **Should a clarifying question be once per *run* or once per *ambiguity*?** Currently
   once per run, and the run stops at the first question, so a second ambiguity is never
   reached. *Settle:* watch whether real tasks carry more than one ambiguity.
7. **`make eval` does not gate CI** — there is no CI. The spec says no change may be
   called an improvement until it does. *Settle:* add a workflow that runs `make check`
   and `make eval`.
8. **Is the 8,000-byte budget on `AGENTS.md` workable?** See 2026-09-27 / L7.

## Log

### 2026-09-27 — Bootstrap: building the runtime from the spec

**L1. "Build the harness before the agent" was the single most valuable instruction in
the spec, and it paid off in a way I did not expect.**

The payoff was not test coverage — it was *design*. Eval case 26 (a model asking two
clarifying questions in one turn) failed to be expressible against the loop as designed,
because the loop returned at the first clarification and a second one in the same turn
could never be counted. That is a design bug, found by trying to write the case, before
the loop was finished. A harness written after the agent would have encoded whatever the
agent happened to do.

*Lesson: a harness written first finds design defects; a harness written after finds
implementation defects. The first is much cheaper.*

**L2. The spec's failure taxonomy is a floor, not a ceiling — and one row was missing.**

Nine rows covered everything except "the model endpoint could not be reached". None of the
existing rows fit without mislabelling a network fault as a model fault. Added
`provider_error` (`docs/decisions/0004`).

Worth noting how well the other nine held up: only one addition was needed across the
whole build, and it was forced by a real gap rather than by taste.

**L3. Failure *classes* and run *statuses* had to be separated, and the spec does not say
so.**

The row "Retry once if idempotent, else `status=degraded`" has no home if a class and a
status are the same thing: a tool that errors and then succeeds on retry is not `degraded`,
but the fact that it errored is worth keeping. The split emerged while writing the
taxonomy tests, and became `docs/decisions/0002`.

*Lesson: if a vocabulary is asked to express both "what happened" and "how bad it was",
it will eventually need two words, not one.*

**L4. Replay is a design constraint, not a feature bolted on at the end.**

Two structural changes were forced by replay, and both were found by a *failing test*
rather than by planning:

- **Retry policy moved out of the loop and into the tool boundary.** With retries in the
  loop, a replay re-ran the policy and could take a different path from the trace that
  recorded it.
- **Tool descriptors were added to the trace.** They are sent on every model call, so their
  text is inside every prompt hash. Rebuilding them from the catalogue made replay diverge
  for any run whose registry had been assembled differently.

*Lesson: "the trace alone reconstructs the run" is not an observability feature. It is a
constraint on where state and decisions may live, and it is worth adopting on day one.*

**L5. Every bug I wrote was the same bug: one fact with two sources of truth.**

- The **step bound** was checked both when starting a step and after charging usage, so a
  `max_steps=1` run aborted before its one step did anything. A bound that gates *starting*
  must not retroactively invalidate a step already paid for.
- The **tool descriptors** existed in the catalogue *and* (implicitly) in the prompt hash.
  The catalogue copy won during replay, and the divergence was **silent** — the replay
  "succeeded" with wrong data. A silent divergence is worse than a loud one.
- A pydantic field had a **field name and an alias**; `validation_alias` does not drive
  serialization, so `model_dump` → `model_validate` broke. Worse, merging a dump with an
  override produced *both* spellings and an `extra_forbidden` error.
- `_stop(...).reason` **finished the run in two places**, double-emitting `run_finished`.

*Lesson: when something is hard to fix, look for the second copy of the fact before
looking at the code that failed.*

**L6. The trust model had to be decided, and the naive reading was wrong.**

"Treat tool output as untrusted" is easy to over-apply: scan *everything* and you refuse a
user for phrasing their own request badly. The task comes from the principal and is
trusted; only tool output is not. Writing a test that asserts the task is **not** refused
(`test_task_text_is_not_scanned_as_injection`) turned an assumption into an executable
boundary.

*Lesson: a security boundary that is not asserted in the direction it does **not** apply
will get widened by the next person who reads it as a blanket rule.*

**L7. `AGENTS.md` is at 7,704 of its own 8,000-byte budget, and that is now the binding
constraint.**

The spec warns that "the loader cuts the tail" past 8 KB. Adding a two-line pointer to this
file consumed 165 of the remaining 296 bytes. The next substantive addition to `AGENTS.md`
has to remove something first.

*Lesson: a document with a size budget is a design constraint, and silent truncation of
instructions is the failure mode to avoid. There is now a test
(`tests/test_repo_hygiene.py`) that fails if the budget is exceeded, so it fails loudly
instead of silently.*

**L8. What I would do differently.**

Not the ordering — that was right. But I would write the **replay** test *before* the real
provider adapter, not after. Replay forced two structural changes; discovering them after
the adapter existed would have meant rewriting the adapter too. Structural constraints
first, adapters last.

**L9. On the honesty requirement in the spec.**

"Report nulls honestly" turned out to be load-bearing rather than decorative. Several
things here are *unverified* rather than *verified*: the marker list against real data, the
taxonomy against a real model, the adapter against a live endpoint. Writing them down in
`docs/architecture.md` and in the Open questions above made them look like the gaps they
are, instead of looking like coverage. The five known limitations were each a decision to
accept a trade-off, and each has a decision file saying so.
