# 0023 — A step may not dispatch an unbounded number of tool calls

Date: 2026-09-28
Status: accepted

## Context

Found by asking a mechanical question rather than reading the spec: **which declared bounds can
actually bind?** The project already had that instinct once — decision
[0015](0015-cost-budget-must-bind.md) refuses a configuration whose cost bound cannot fire. This
is the same question asked of the *loop*.

`max_steps` bounds **steps**. Nothing bounded the calls inside one. A single model response may
carry an arbitrary number of tool calls, and the loop dispatched every one of them:

```python
for call in response.tool_calls:  # no bound on this
    record = self.tools.dispatch(...)  # no budget consulted in here
```

Measured, not reasoned: a stub emitting 40 tool calls in one step dispatched **all 40**, with
`max_steps` set to 6 and a budget that could afford two model calls. The clock is read at step
boundaries and never inside the dispatch loop.

The consequence is that **all four budget bounds are evaluated at step boundaries, so nothing
inside a step is interruptible.** A step that dispatches `n` tool calls runs for up to
`n x timeout_s` before any bound is consulted — 40 calls at a 5-second timeout is over three
minutes against a `max_wall_clock_s` of 120.

The spec says, in as many words, that **no unbounded path may exist**. This was one.

## Decision

**`MAX_TOOL_CALLS_PER_STEP = 16`**, enforced in `_handle_tool_calls`. Calls beyond it are not
dispatched; the excess is recorded and the model is told.

Four choices, each with a reason:

- **A constant, not a fifth budget bound.** It caps a single turn structurally, the way
  `MAX_ATTEMPTS` caps retries — it is not a policy knob a configuration should tune. It also
  avoids re-opening the spec's "all four bounds, none optional" contract for something that is
  not a budget in the same sense.
- **A count, not a clock check.** This is the important one. A budget check inside the dispatch
  loop would have made the *number of dispatches* depend on the wall clock — and
  `runtime/replay.py` says plainly that a wall-clock-bounded run only replays identically under a
  deterministic clock. Putting a clock in the dispatch loop would have widened that requirement
  to every run that dispatches many calls, which is a far worse bug than the one being fixed.
- **`budget_exhausted`, not a new taxonomy row.** This *is* a budget, and
  `budget_exhausted:{limit}` already means "a declared bound stopped something". Reusing it keeps
  the taxonomy closed, and the detail names the limit.
- **`degraded`, and the model is told.** The run can continue — the model gets a transcript in
  which sixteen calls were answered — so it is not `partial`. But it is told *in-band*, with a
  runtime note, because a turn that was silently half-answered is the failure this design keeps
  refusing to have. It can ask again for what it still needs.

## Consequences

- Five tests in `tests/test_loop.py`, including a guard that a turn at *exactly* the limit is
  untouched, and one that a capped run still replays identically — the determinism claim, tested
  rather than asserted.
- The four budget bounds keep their meaning and are still evaluated at step boundaries. What
  changed is that a step can no longer be arbitrarily large, so the overshoot is now bounded by
  `MAX_TOOL_CALLS_PER_STEP x timeout_s` rather than by whatever a model chose to emit.
- The cap is invisible to every existing case: the live suite's models emit at most five calls in
  a turn, and the golden set emits at most two.

## Alternatives considered

**Check the budget before each dispatch.** The principled-sounding fix, and the wrong one for the
reason above: it makes the dispatch count clock-dependent and breaks replay exactness for any run
that hits it. Correctness of the record outranks tightness of the bound.

**Bound the calls in the provider adapter.** Rejected: the adapter's job is to report what the
model said, not to censor it. The loop owns the loop's bounds.

**Leave it and document it.** Rejected: the spec's claim is explicit, the gap is a real unbounded
path, and the fix is a counter and a note.
