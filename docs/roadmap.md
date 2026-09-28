# Roadmap

Where this is going, in dependency order. Unlike `docs/decisions/`, this is a **living
document** — edit it as the plan changes. What actually happened belongs in
`AGENTS_LEARNING.md`; what we decided belongs in `docs/decisions/`.

## Where we are

The runtime is complete against the spec: a bounded loop, a tool boundary with a
confirmation gate, four enforced budget bounds, replayable traces, a tiered injection
guardrail, redaction, a golden set, and CI that `main` requires. `main` is protected, so
every change arrives through a pull request with a green check.

Run `make ci` for the current counts. They are deliberately **not** written down here: a
hard-coded number in prose goes stale the moment anyone adds a test, and this line proved
it — it carried a count that was already wrong, was "refreshed" to a second wrong count, and
had to be rolled back. The rollback restored the first wrong number, because rollback
restores a revision, not correctness.

**Phase 1 is done, and Phase 2's first domain is done.** It has met real data three times, and
each meeting found something the tests could not:

1. **A real endpoint, four API calls.** Two defects in the token accounting — the estimate
   omitted the tool schemas and used the prose ratio for JSON; the two context thresholds
   measured the same quantity when they should measure different ones. Both fixed with the
   numbers in [decisions/0005](decisions/0005-token-estimation.md).
2. **A live property suite** against a real model, ten cases, opt-in and not in CI. It found
   that `refused` is vendor-signalled: a model that declines *in words* without setting the
   flag is recorded as `ok` ([decisions/0019](decisions/0019-refusal-is-vendor-signalled.md)).
3. **A real domain** — triage, in `configs/triage.yaml`. It found two prompt gaps, falsified an
   assumption about what the domain meant, and broke a runtime branch that every test had been
   passing over: **every run that used the structured-output repair pass was unreplayable**
   ([decisions/0021](decisions/0021-order-independent-serialisation.md)).

The third is the argument for Phase 2. The repair pass runs only when a model is *almost* right,
and a stub never takes that branch — **a stub is a specification of a model, and a specification
does not make mistakes.** Coverage would not have shown it.

What has **still** not met real data, and this list is the honest half:

- **The 33 golden-set cases are stub-driven.** They test the runtime's contract, which is what
  they are for, but they say nothing about a real model's behaviour on those tasks.
- **The marker corpus is hand-built.** Roughly a hundred real tool results now exist in
  `.traces/`, all from five tools in one domain — not the few hundred, and not the variety, that
  step 3's trigger asks for.
- **One provider on one day is not a range of providers.** The vendor's actual behaviour remains
  the one thing the mock transport cannot cover.
- **Context at real sizes**, concurrency, and streaming are all Phase 3 and all untouched.

The distinction matters. "It works against a real model" is a fact, and a much narrower fact
than it sounds.

## Phase 1 — prove the foundation against reality

Ordered by what could invalidate the most work, because that is what makes a step worth
taking first.

### 1. Run a real model through the existing cases

**Why first.** Everything here rests on the assumption that a model behaves the way the
stub does: one tool call per turn, arguments that validate, text that parses. If a real
model does not, the failure taxonomy is a hypothesis and the eval harness is measuring a
fiction. This is the cheapest step that can invalidate the most.

**Done, 2026-09-27.** A real endpoint has run the loop end to end — a plain answer and a
native tool call, both correct — and real traces replay exactly. Three things came out of it,
and each is the kind of result this step existed to produce:

1. **Two defects, in four API calls.** The token estimate omitted the tool schemas and used
   the prose ratio for JSON; the two context thresholds measured the same quantity when they
   should measure different ones. Both fixed, with the numbers in
   [decisions/0005](decisions/0005-token-estimation.md).
2. **A second scorer mode**, and with it a decision about what each suite is for: the golden
   set is the runtime's *contract* (exact, deterministic, in CI) and the live suite is the
   *model's* behaviour (properties, opt-in). Real traces are committed as fixtures and
   replayed in CI, so the question has a CI-safe answer too
   ([decisions/0018](decisions/0018-two-eval-suites.md)).
3. **A taxonomy gap, found by the live suite on its first run.** `refused` is
   vendor-signalled: a model that declined *in words* was recorded as `ok`. The model behaved
   correctly and the runtime called it an ordinary answer
   ([decisions/0019](decisions/0019-refusal-is-vendor-signalled.md)).

**And the answer to the question this step was really asking** — does a real model produce
something the ten rows cannot describe — is *no, but a class can go undetected*. The taxonomy
was not missing a row; one of its rows depends on the vendor reporting something.

A real model also **resisted an injected instruction** on both live runs: a tool returned
plausible prose telling it to call `write_note`, the payload was verified to evade the marker
scan, and the model treated it as data. That is the semantic-injection gap from
[decisions/0006](decisions/0006-untrusted-content-policy.md) measured rather than asserted —
two runs is not a guarantee, and it is now a committed fixture so it stays true.

### 2. Rollback, scoped to a local-only project

The last spec gap. The spec asks for "one command, documented, and performed once before
you need it"; with no deploy target, that means reverting a revision through the protected
flow, not redeploying an image. Scope recorded in
[decisions/0013](decisions/0013-rollback-scope.md).

**Trigger:** none — done as part of writing this.

### 3. The injection markers against real tool output

`make markers` measures the rule against a corpus I wrote by hand. Real tool output will
contain phrases nobody anticipated, and the guardrail is fail-closed, so a false positive
refuses a legitimate run. This is open question 1's successor and it needs Phase 1 first:
there is no real tool output until there are real runs.

**Trigger:** a few hundred real tool results in `.traces/`.

### 4. The redaction patterns against real traces

Same shape as 3, opposite failure economics: over-redaction is the acceptable error, but a
set that eats too much makes traces useless. Open question 8.

**Trigger:** real traces to measure against.

## Phase 2 — make it useful for one real task

The runtime is generic by design and has no domain. It becomes *something* when a
configuration gives it one.

### 5. Real tools — done, 2026-09-28

The tool set was `calculator`, `clock`, `echo` and `write_note` — useful for testing the
boundary, useless for real work. `tools/triage.py` is the first set that does a job:
`list_messages`, `read_message` and `escalate`, the last being a side effect and therefore gated.

A tool really is a file plus a name in `tools/builtin.py`. The core did not change, which is the
design being cashed in for the first time.

### 6. A task worth doing — done, 2026-09-28

`configs/triage.yaml` — triage a support inbox. One domain, one configuration, four message
fixtures, and four live cases that encode what "correct" means for it.

It was the right first domain for a reason worth keeping: **it exercises the four things this
runtime paid for** — the untrusted envelope, the injection scan, structured output, and the
confirmation gate — and its correctness is assertable, because a category is right or it is not.

**What it found, in its first hour:**

1. **Two prompt gaps**, one per run. The model asked permission before escalating (redundantly —
   the caller's token had already authorised it), and then, once that was fixed, classified
   correctly and did not escalate. Its *judgement* was right every time; its *compliance with the
   action* was the unreliable half.
2. **A live case that encoded an expectation the domain does not support.** It asserted that a
   duplicate-charge message needs no human; the model escalated, and the model was right — a
   refund needs a person. The case was replaced.
3. **A real bug in the runtime, and the biggest find of the phase.** The first live run ended in
   `ReplayDivergence`: every run that used the structured-output **repair pass** was
   unreplayable, because the repair note serialised the schema in arrival order and a
   configuration has two key orders depending on which door it came through
   ([decisions/0021](decisions/0021-order-independent-serialisation.md)).

The third is the whole argument for this phase. The repair pass is the branch that runs when a
model is *almost* right — and a stub never takes it, because a stub is a specification of a model
and a specification does not make mistakes. **Every test in this repository was written against
code that behaves.**

**What to resist here:** adding a second domain before the first one works. The
configuration mechanism makes that tempting and it is how a generic runtime becomes a pile
of half-finished verticals.

## Phase 3 — only once Phase 1 and 2 hold

- **Context at real sizes — done, 2026-09-28.** Summarisation and dropping had only ever been
  exercised with synthetic lengths; measured across every trace, **neither had ever fired**.
  Both are now driven with real content:
  [decisions/0022](decisions/0022-summarisation-is-truncation.md) covers summarisation (the
  mechanism is truncation, the marker is load-bearing, the recovery belongs to the model) and
  [decisions/0024](decisions/0024-dropping-takes-the-request-with-it.md) covers the hard ceiling
  (dropping took requests away from their results, and the fix keeps the most recent group and
  refuses when even that cannot fit).

  **What is still open from this work:** tool-call *arguments* are unbounded in the transcript
  and nothing summarises the assistant turn, so one step can add ~65,000 tokens that only the
  hard ceiling can remove. That is the real fix for the incoherence 0024 treated the symptom of,
  and it is recorded there rather than done.
- **Concurrency.** The service handles one run at a time. The trace writer assumes one
  writer per file, which holds until it does not.
- **Streaming.** Not needed for a request/response runtime; needed the moment there is a UI.

## Deliberately not on this roadmap

Not "later" — **not**, unless something changes:

- Multi-agent orchestration. One loop, bounded, is the whole point.
- Memory across runs. Every run starts from the task; the trace is the record, not the
  context.
- A UI. The service and the CLI are the interfaces.
- Fine-tuning. Not a substitute for a smaller configuration.
- A second provider adapter before the first one has run against a live endpoint.

## The risk worth naming

Every step in Phase 1 can come back negative, and that is the point of doing them first.
If a real model does not respect the envelope, or returns three tool calls when the budget
allows two, or wraps JSON in prose the parser cannot unwrap, then work built on top of the
current assumptions is work that has to be redone. Phase 2 is where the interesting product
questions live, and it is deliberately second.
