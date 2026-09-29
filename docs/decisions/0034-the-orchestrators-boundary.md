# 0034 — The orchestrator's boundary, and what it absorbs

Date: 2026-09-29
Status: accepted

## Context

`run()` has always said it never raises for a task-level problem. That was true **only while every
collaborator kept a promise nothing checked**, and the promises are stated in docstrings:

* `Provider` — "return one normalised model turn, **or raise `ProviderError`**";
* the tool boundary — "never raises for a tool-level problem: every failure becomes a record";
* `Budget` — raises `BudgetExceeded`, and the loop catches it.

Measured, before any of this existed:

```
provider raises ValueError        -> RAISED ValueError: surprise
provider raises OSError           -> RAISED OSError: connection reset
tool boundary raises RuntimeError -> RAISED RuntimeError: boundary exploded
provider raises KeyboardInterrupt -> RAISED KeyboardInterrupt        (correct)
```

An adapter leaking a socket reset took the whole run down with an exception. **A guarantee that
depends on the other party behaving is not a guarantee** — the same argument
[0015](0015-cost-budget-must-bind.md) makes about a bound that cannot fire, applied to a promise
instead of a number.

## What was built

The boundary is now stated where it is owned — the module docstring of `runtime/loop.py` — in four
clauses, and each one is enforced:

1. **It returns a `RunOutput` for anything that goes wrong in the task.** Two things still escape, on
   purpose: a `RunLog` that cannot write (there is no record to report a status in), and a process
   stopping.
2. **A collaborator that breaks its own contract is absorbed at the seam** — the provider's and the
   tool boundary's, because those two are implemented outside this package and their promises are
   unverifiable from here. The failure is classified with the **existing taxonomy** and the exception
   *type* is named in the detail. Absorbing is not hiding.
3. **This module's own bugs are not absorbed.** A defect in the loop must be loud, not filed as a
   task failure; the seam catches someone else's mistake, and only there.
4. **Nothing is absorbed before `run_started`.** A boundary that cannot even describe itself is a
   composition error, and there is no run yet for it to fail.

The tool seam **synthesises the record the boundary should have returned** rather than inventing a
second failure path: the run then continues through the existing interpretation (optional means
degraded, required means partial), and the trace still holds one record per dispatch — which is the
log boundary's first clause, and a dispatch that left no record would break it silently.

## The near-miss that shaped it

The first version of the catch-all broke two replay tests, and they were right to break.
`ReplayDivergence` is an `Exception`, so `except Exception` swallowed it and reported the divergence
as `status=failed, reason=provider_error` — **a plausible-looking lie about a defect in this
project.** Its own docstring had said it: *"a divergence must escape the loop rather than be reported
as one more way a run can end."* "Not a `ProviderError`" stopped being enough the moment the seam
began catching everything else.

So the escape hatch is a **type**, not an absence: `ProviderSignal` in `providers/base.py`, with
`ReplayDivergence` deriving from it. An implementation raising one has not failed — it is telling the
caller something about the harness — and the loop re-raises it untouched.

**The only reason this is not shipped behaviour is that two tests already existed for it.** A
catch-all at a seam is exactly the change that quietly destroys someone else's invariant, and the
invariant here was defended by tests written for a different purpose.

## Why no new failure class

An unexpected exception from a provider *is* a provider error from this seat — "could not be reached
or returned a non-conforming payload" covers a leaked socket reset — and a boundary that raised is a
tool error. So the taxonomy is unchanged, and `test_every_failure_class_has_a_test` needed no
touching. Where a class genuinely has no home, [0004](0004-failure-taxonomy-provider-error.md) is the
precedent for adding one; this did not need it.

## Evidence

Four mutations, each a one-token edit, each restored from a byte copy with the hash verified:

| mutation | caught by |
|---|---|
| the provider catch-all removed | `test_a_provider_that_raises_something_else_is_absorbed` |
| the tool catch-all removed | `test_a_tool_boundary_that_raises_is_absorbed_into_a_record` |
| the signal escape hatch removed | `test_a_provider_signal_is_not_absorbed` (+ both replay tests) |
| a divergence stops being a signal | `test_a_replay_divergence_is_a_provider_signal` |

**The probe harness was wrong first**, and said so: its self-test inserted a *duplicate*
`except ProviderSignal:` clause and reported `BLIND`. Python allows duplicate except clauses — the
first wins — so the mutation changed nothing. The harness was broken; the guard was fine.

## What this does not do

- **It does not make the loop absorb its own failures.** A bug in `_interpret` still raises, and
  `test_the_loop_does_not_absorb_its_own_bug` pins that.
- **It does not validate the provider's *response*.** A provider returning a nonsense `ModelResponse`
  is a different problem, and `ModelResponse` is a validated contract already.
- **It does not change the taxonomy**, so no eval case and no new class.
