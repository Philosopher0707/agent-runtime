# Architecture

The runtime is generic; every capability is a configuration. This document is the
component map and, more importantly, the reasoning behind each boundary — because a
boundary without a reason is just a folder.

## The shape of a run

```
                       ┌──────────────────────────────────────────────┐
   RunRequest ────────▶│ runtime/loop.py                              │
   (task, config,      │ step · dispatch · terminate                  │
    confirmation)      │                                              │
                       │  ┌─────────────┐    ┌─────────────────────┐  │
                       │  │ budget.py   │    │ context/assembler   │  │
                       │  │ 4 bounds    │    │ + sanitize          │  │
                       │  └─────────────┘    └─────────────────────┘  │
                       └───────┬───────────────────────────┬──────────┘
                               │                           │
                    ┌──────────▼─────────┐      ┌──────────▼──────────┐
                    │ providers/base.py  │      │ tools/registry.py   │
                    │  the Provider      │      │  declare · validate │
                    │  protocol          │      │  dispatch + attempts│
                    └──────────┬─────────┘      └──────────┬──────────┘
                               │                           │
              ┌────────────────┼──────────────┐   ┌────────┴─────────┐
              │                │              │   │ tools/builtin.py │
        providers/stub   openai_compat   replay   │ tools/scripted   │
                                                    └──────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │ runtime/trace.py    │  one append-only JSONL per run
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ runtime/replay.py   │  the trace is the only input
                    └─────────────────────┘
```

`runtime/factory.py` is the composition root: the only module that names a concrete
provider or tool implementation. Everything upstream depends on a protocol.

## Authority boundaries

Each component owns exactly one thing.

| Component | Owns | May not |
|---|---|---|
| `runtime/loop.py` | Orchestration: step, dispatch, terminate | Contain domain logic, call a provider directly, know about specific tools |
| `providers/base.py` | The `Provider` protocol and the prompt hash | Know a vendor's wire format |
| `providers/openai_compat.py` | One vendor's wire format | Decide a run status, or retry |
| `tools/registry.py` | Registration invariants, argument validation, dispatch policy | Contain a tool implementation |
| `tools/<name>.py` | One tool: schema, side-effect class, implementation | Reach outside its declared scope |
| `context/` | Assembling and truncating context; token accounting | Drop the system prompt or the task, ever |
| `runtime/budget.py` | The only code that can abort a run | Be bypassed; no unbounded path may exist |
| `runtime/trace.py` | The only writer of the run record | Be optional, or rewrite a line |
| `evals/` | The only authority on whether a change helped | Be skipped in CI |

## The one rule, and how it is kept

> **The core loop contains no domain logic and no provider-specific code.**

The loop knows four things: the `Provider` protocol, the `ToolBoundary` protocol, the
budget, and the failure taxonomy. It knows one tool *name* — `ask_clarification` — and
that is protocol rather than capability: it is intercepted before dispatch so that "the
model wants to ask something" is a countable event rather than a sentence matched out of
prose.

The rule is checkable rather than aspirational: `grep -r "openai\|anthropic\|http" runtime/loop.py`
returns nothing, and every vendor string lives in `providers/`.

## Where the load-bearing invariants are enforced

| Invariant | Enforced in | Tested by |
|---|---|---|
| Four bounds, none optional | `BudgetConfig` (no defaults) + `Budget.check` | `test_budget.py` |
| The system prompt and task are never dropped | `ContextAssembler.build` (raises rather than lies) | `test_context.py` |
| Tool output is never instructions | `context/sanitize` + the loop's guardrail | `test_sanitize.py`, `test_taxonomy.py` |
| Every injection marker earns its place | the ablation test | `test_marker_precision.py` |
| A mutating tool cannot run without a token | `ToolRegistry.register` **and** `dispatch` | `test_tools.py` |
| The model cannot authorise a side effect | dispatch overwrites the field; it is never advertised | `test_tools.py` |
| A trace alone reconstructs the run | `TraceWriter` records responses, outcomes, and descriptors | `test_replay.py` |
| Replay never executes a tool | `RecordedDispatcher` | `test_replay.py` |
| A class with no test is undiscovered | the taxonomy coverage test | `test_taxonomy.py::test_every_failure_class_has_a_test` |
| CI and a local run are the same thing | the workflow calls `make ci` and runs nothing else | `test_ci_contract.py` |
| Nothing is logged that redaction was asked to remove | `TraceWriter.emit` — the one chokepoint every event passes | `test_redaction.py` |

## The trust model

Three different levels of trust, applied consistently:

1. **The task is trusted.** It comes from the principal. Text in it that looks like an
   injection is the principal's own phrasing, not an attack.
2. **Tool output is not trusted.** No exception, including for our own tools. It is
   always wrapped in a delimited envelope and always scanned.
3. **The final answer is policed.** It may leak; if it echoes the system prompt, the run
   is refused.

Rationale in [decisions/0006](decisions/0006-untrusted-content-policy.md) and
[decisions/0009](decisions/0009-trust-model.md). The detector behind the second rule is
tiered and measured — [decisions/0011](decisions/0011-marker-precision-tiering.md).

**Redaction is a fourth, separate boundary, and it applies to the opposite side.** The
prompt is never redacted — redacting it would silently change the task. The *trace* is,
because it is the durable artefact. A redacted trace cannot be replayed, so the two are
mutually exclusive and the choice is explicit:
[decisions/0012](decisions/0012-pii-context-and-redaction.md).

## Status is derived, not asserted

Failure *classes* are recorded; the run *status* is derived from them by precedence
(`refused > failed > partial > degraded > ok`). This separation is what lets a recovered
tool error leave the status `ok` while the class survives in the record. Nothing is
hidden: every individual claim remains in `RunOutput.failures`.

Rationale in [decisions/0002](decisions/0002-status-precedence.md).

## The division of labour that makes replay exact

| Question | Answered by |
|---|---|
| How many times should this tool be tried? | `tools/registry.py` |
| Can the run continue without it? | `runtime/loop.py` |

Splitting it this way is what lets a recorded call be *replayed* as its recorded outcome
sequence rather than re-decided. If the loop owned retries, a replay would re-run the
policy and could take a different path from a trace that says otherwise.

## Adding a capability

A capability is a configuration, never a branch in the core.

1. Add a tool under `tools/` and name it in `tools/builtin.py` — if it changes state,
   declare `side_effect = True` and put a required `confirmation_token` in its args model.
   Registration will refuse it otherwise.
2. Add `configs/<name>.yaml`. All four budget bounds are mandatory.
3. Add cases under `evals/cases/` that force the failure classes it can hit.
4. Run `make ci`. If the score moves, say so in the commit message.
5. If you touched the injection markers, `make markers` will tell you. It fails below
   threshold, and refuses a marker that catches nothing.

## What is not here

Not a chatbot. No conversation memory across runs. No user accounts or UI. No
multi-agent orchestration. No fine-tuning. No provider-specific code in the core. No
framework — the loop is 40 lines of orchestration and is meant to stay that way.

## Known limitations

Stated rather than discovered later:

- **Token accounting is a heuristic** (`chars / 4`). Documented in
  [decisions/0005](decisions/0005-token-estimation.md).
- **A timed-out in-process tool's thread keeps running.** Documented in
  [decisions/0007](decisions/0007-in-process-tool-timeouts.md).
- **Structured output validates a JSON Schema subset**, not the whole standard.
  Documented in [decisions/0008](decisions/0008-structured-output-subset.md).
- **A wall-clock-bounded run only replays identically under a deterministic clock.**
  Replay takes an injectable clock for exactly this reason.
- **Redaction is pattern-based, and off by default.** It catches shapes, not meanings: a
  name in prose, or a number in a format the patterns do not list, passes through. Enabling
  it makes the trace non-replayable, which is why the default is off rather than on
  ([decisions/0012](decisions/0012-pii-context-and-redaction.md)).
- **`openai_compat` is verified against a mock transport, not a live endpoint.** The
  parsing and error paths are covered; the vendor's actual behaviour is not.
- **The injection scan is a tripwire with a measured precision limit.** It is lexical, so
  it cannot distinguish a payload from prose that *quotes* a payload, and it misses
  payloads written as ordinary plausible prose. Measured at 0% false positives and 100%
  recall on a hand-built corpus of 26 benign and 15 hostile samples
  ([decisions/0011](decisions/0011-marker-precision-tiering.md)); the corpus is not real
  tool output. The envelope, not the scan, is the primary defence.
