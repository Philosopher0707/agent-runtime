# 0035 — The seam absorbs what it owns

Date: 2026-09-29
Status: accepted

## Context

[0034](0034-the-orchestrators-boundary.md) made the loop absorb a collaborator that breaks its
contract, and said plainly that the seam catches someone else's mistake **and only there**. That
leaves a question one layer down: what does the *tool* seam promise, and is it true?

`ToolRegistry.dispatch` has always said *"Never raises for a tool-level problem: every failure becomes
a record."* Attacked, it was false in four ways:

```
a field_validator raising TypeError       -> RAISED TypeError: a validator bug
dispatch after the registry was closed    -> RAISED RuntimeError: cannot schedule new futures
the injected monotonic clock raises       -> RAISED RuntimeError: clock broke
the injected backoff sleep raises         -> RAISED RuntimeError: sleep broke
```

Two of those are the tool's, two are not, and the difference is the whole decision.

## What was built

**Absorbed, because they are the tool's or the registry's own:**

* **The args model.** A `field_validator` raising a `TypeError` escapes pydantic's wrapping, so
  `model_validate` can raise something that is not a `ValidationError`. It is the tool's schema code,
  so it is the tool's record — `NOT_EXECUTED`, with the exception type named.
* **The executor.** The registry owns the pool, so `submit` refusing the work (the registry was
  closed, or the pool is gone) is a failure for *it* to report rather than for the caller to catch —
  an `ERROR` record naming the type.

**Not absorbed, and now stated as such:** the injected clock and the injected backoff `sleep`. Those
are the composition root's own callables, and a registry cannot file *"the clock does not exist"* as
a tool record any more than a run can be failed by a clock that does not exist. They are a
**composition error**, the same class as a boundary that cannot describe itself, and
`runtime/loop.py` absorbs them at its own seam so a run still ends with a status rather than an
exception.

The line is 0034's rule applied one layer down: **the seam catches someone else's mistake, and the
component's own bugs stay loud.** A catch-all around `dispatch` would have been simpler and would have
filed a defect in the retry loop as a tool error.

## And registration proves the tool can describe itself

`register()` now calls `describe()` inside the same refusal it applies to every other invariant. The
descriptor is built from the args model and is sent on **every request**, so a tool whose schema
cannot be rendered is a run that dies at setup — before anything is recorded, which is the hardest
place to attribute a failure. Found by attacking it: a tool overriding `describe()` to raise
registered cleanly and then took the run down in `_Orchestrator.__init__`.

## Evidence

Three mutations, one-token edits, each restored from a byte copy with the hash verified:

| mutation | caught by |
|---|---|
| the args-model catch-all removed | `test_a_tool_whose_args_model_raises_is_a_record_not_a_crash` |
| the executor catch-all removed | `test_dispatch_after_close_is_a_record_not_a_crash` |
| registration stops proving describability | `test_a_tool_that_cannot_describe_itself_cannot_be_registered` |

## What this does not do

- **No new failure class.** A tool's args model raising is `invalid_arguments`; a dead executor is a
  `tool_error`. The taxonomy is unchanged.
- **It does not make the registry absorb its own retry logic.** A bug in the loop that decides how
  many times to try is still a bug, and it should be found rather than recorded as a tool outcome.
