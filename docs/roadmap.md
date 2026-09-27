# Roadmap

Where this is going, in dependency order. Unlike `docs/decisions/`, this is a **living
document** — edit it as the plan changes. What actually happened belongs in
`AGENTS_LEARNING.md`; what we decided belongs in `docs/decisions/`.

## Where we are

The runtime is complete against the spec: a bounded loop, a tool boundary with a
confirmation gate, four enforced budget bounds, replayable traces, a tiered injection
guardrail, redaction, a golden set, and CI that `main` requires. 271 tests, 33 eval cases,
two merged PRs, `main` protected.

**And nothing in it has met real data.** Every eval case is stub-driven. A stub is a
*specification* of a model, not a model — it cannot violate our assumptions, and real ones
can. That is the single most important fact about this project's current state.

## Phase 1 — prove the foundation against reality

Ordered by what could invalidate the most work, because that is what makes a step worth
taking first.

### 1. Run a real model through the existing cases

**Why first.** Everything here rests on the assumption that a model behaves the way the
stub does: one tool call per turn, arguments that validate, text that parses. If a real
model does not, the failure taxonomy is a hypothesis and the eval harness is measuring a
fiction. This is the cheapest step that can invalidate the most.

**What it needs that does not exist yet:**

- An API key (`configs/openai_compat.yaml` is written but has never been pointed at a live
  endpoint).
- **A second scorer mode.** The 33 cases assert *exact* outcomes — `output_exact`,
  `model_calls: 2`, `attempts: 1` — because the stub is deterministic. A real model is not,
  so a live run must assert *properties*: the status, the failure classes, whether a tool
  was called, whether the guardrail held. The stub suite stays as the contract test; the
  live suite is a separate thing, and mixing them would make both meaningless.
- A recorded-trace fixture from a real run, so the live suite can be replayed in CI without
  a key.

**Trigger:** an API key. Nothing else blocks it.

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

### 5. Real tools

The current set is `calculator`, `clock`, `echo`, `write_note`, `fetch`. Useful for testing
the boundary; useless for real work. A tool is where a capability lives, and adding one is a
file plus a name in `tools/builtin.py` — no core change, which is the design being cashed in
for the first time.

### 6. A task worth doing

One domain, one configuration, tools that do real work, and eval cases that encode what
"correct" means for it. The first time the eval harness grades something whose answer is not
known in advance.

**What to resist here:** adding a second domain before the first one works. The
configuration mechanism makes that tempting and it is how a generic runtime becomes a pile
of half-finished verticals.

## Phase 3 — only once Phase 1 and 2 hold

- **Context at real sizes.** Summarisation and dropping have only been exercised with
  synthetic lengths. The soft/hard thresholds and the 400-character adjacency window are
  judgement calls that real prompts will test.
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
