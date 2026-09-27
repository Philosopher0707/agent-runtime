# 0018 — Two eval suites, because they test different things

Date: 2026-09-27
Status: accepted

## Context

The golden set asserts *exact* outcomes — `output_exact`, `model_calls: 2`, `attempts: 1` —
because the stub is deterministic. That is what makes it a contract test: given these model
outputs, the loop does exactly this, every time.

A real model is not deterministic. A live run cannot assert those things without asserting the
model's wording, and a suite that fails because a model wrote `21 × 2 = 42` instead of `42` is
a suite nobody would keep.

## Decision

**Two suites, with different jobs and different assertions.**

| | golden set (`make eval`) | live suite (`make live`) |
|---|---|---|
| tests | the runtime's **contract** | the **model's** behaviour |
| asserts | exact values | properties |
| deterministic | yes | no |
| in `make ci` | yes | **no** — needs a key |

The live property vocabulary is deliberately small and named after the question it answers:

```
status_in / status_not_in          tools_called_includes / _excludes
output_matches (regex)             output_min_chars
failure_classes_exclude            steps_at_most / model_calls_at_most
notes_written
```

Anything needing an exact value belongs in the golden set. Adding property assertions to the
golden set was considered and rejected: `make eval` gates at threshold 1.0, and mixing exact
and property assertions in one file would make "what does 1.0 mean" ambiguous.

## The CI-safe half

A live suite cannot gate a push, so the same question gets a second answer that can:
**recorded real traces, committed and replayed** (`evals/fixtures/`, checked by
`tests/test_recorded_runs.py`). Replaying one asserts the canonical projection is identical to
the recorded run — same status, same tool outcomes, same prompt hashes, same accounting.

That answers *does the runtime handle what a real model actually produced* with no key, no
network and no non-determinism.

**The promotion path closes the loop:**

1. `make live` — run against a real model. Every run writes a trace.
2. Read the trace. If the behaviour is worth keeping, copy it into `evals/fixtures/`.
3. CI now covers that behaviour forever, for free.

Four fixtures are committed from the first live runs, including one where a real model
**resisted an injected instruction** — a behaviour worth pinning.

## Consequences

- `make ci` covers the contract and four real recorded runs. `make live` covers model
  behaviour on demand.
- A live case is where a *belief about models* gets tested rather than asserted. It found
  something on its first run — see
  [decisions/0019](0019-refusal-is-vendor-signalled.md).
- Live traces accumulate in `.traces/live/` (gitignored). Promoting one is a deliberate act,
  which is right: a fixture is a claim that this behaviour should not regress.

## Alternatives considered

**Relax the golden set to properties.** Rejected: it would destroy the contract test's value.
The stub suite's exactness is what catches a change to the loop, and a property suite cannot
catch "the loop now takes one extra step".

**Run the live suite in CI.** Rejected: it needs a key, it costs money per push, and a
non-deterministic gate is one people learn to ignore. A gate that fails for reasons unrelated
to the change is worse than no gate.

## Addendum, 2026-09-27 — retiring a fixture

A fixture is a claim that a behaviour should not regress. **That claim expires.** A model that
behaved one way in September may behave differently in November, and the fixture will then fail
for a reason that has nothing to do with this repository.

**Replay distinguishes the two causes**, which is what makes retirement a rule rather than a
judgement call:

| replay the fixture | meaning | response |
|---|---|---|
| reproduces identically | the runtime is unchanged — so a *live* failure means the model moved | retire the fixture, or update the case |
| fails | the runtime regressed | fix the runtime |

That is the whole test, and it falls out of the architecture: a fixture that still replays
proves the runtime still does what it did. So a live case failing *while its recorded trace
still replays* is evidence about the model, not about the code — and the two demand opposite
responses.

Two details:

- **Retire by replacing, not deleting.** Record a new fixture from the current model, so the
  file's history shows what changed and when. A fixture deleted without a replacement loses the
  only record that the behaviour was ever pinned.
- **A fixture stale for a different reason — a schema-version bump — is refused by name**
  ([decisions/0014](0014-trace-schema-version.md)), which is the correct outcome. Re-record it on
  the new build rather than migrating it: the old trace is a fact about the old format.
