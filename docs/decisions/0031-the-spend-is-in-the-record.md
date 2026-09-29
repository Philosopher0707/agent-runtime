# 0031 — The spend is in the record

Date: 2026-09-29
Status: accepted

## Context

[Decisions/0030](0030-starting-a-run-from-inside-a-run.md) built the mechanism that starts a run
from inside a run, verified all four of its answers live, and then found a defect by testing the
claim rather than asserting it:

```
replay reproduces identically: False
replayed cost: $0.0073    recorded cost: $0.0174
```

`$0.0073` is the parent's own spend. The children's `$0.0101` was missing, and the cause was
structural rather than a slip: on replay the spawn is served from the parent's recorded outcome —
the child is not re-run, which is correct and is the whole point — so `Budget.allocate` is never
called and the charge never happens. **The parent's cost was reconstructed from a path that only
executes when a child actually runs.** Third instance of this project's recurring defect: the
repair pass, summarisation, and now this.

0030 deliberately did not fix it. The fix is a mechanism with a schema change, and doing it in the
same commit would have buried it.

## What was built

**A tool can report what it consumed, and the record is what replay restores.**

| where | what |
|---|---|
| `runtime/schemas.py` | `Spend` — `tokens_total`, `cost_usd`, `__add__`, frozen |
| `runtime/schemas.py` | `ToolResult` — `text` plus a `Spend`; the second shape `invoke` may return |
| `runtime/schemas.py` | `ToolCallRecord.spend` — in the record, because replay never runs the tool |
| `tools/registry.py` | `ToolError` gained a keyword `spend`; `dispatch` **adds** across attempts |
| `runtime/budget.py` | `charge(*, tokens_total, cost_usd)` — numbers, not a child `Budget` |
| `runtime/loop.py` | the loop charges each record as it interprets it |
| `runtime/factory.py` | `spawn_runner` **stops** charging — charging there would double-charge a live run |
| `tools/subagent.py` | reports `result.spend`, on the result or on the error |

## Why the charge takes numbers

`Budget.charge` used to take the child's `Budget`. On replay there is no child budget — the child
is never started — so a signature that requires one cannot be called from the path that needs it.
The number has to arrive from the trace, which means it has to arrive as a number. That is not a
tidy-up; it is the defect showing through the API, and the signature is the fix.

## A failed delegation is not a free one

`spawn_agent` raises `ToolError` when a child comes back `failed`, `refused` or `partial` — and
that child still spent what it spent. So the spend travels on the exception as well as on the
result: **both ways an attempt can finish can report one.** A record that says a failed call cost
nothing understates the run, which is the same defect one hop further along.

## Verified against the defect

The same shape of run that produced `$0.0073`, with the stub priced so both units are non-zero:

```
recorded  $0.001852 = own $0.000737 + child $0.001115
replayed  $0.001852   canonical projection identical
```

And the guard is not the agreement of two numbers. `test_the_replayed_cost_comes_from_the_record`
forges the recorded spend, replays again, and requires the replayed cost to move by exactly that
amount — which proves the number is *read from the trace* rather than recomputed by some other
route that happens to agree.

Non-vacuity was checked by simulating the old code (charge in the spawner, not the loop):
`test_a_delegated_run_replays_to_the_same_cost` and
`test_the_replayed_cost_comes_from_the_record` both fail, reporting the parent's own spend alone.

## What this deliberately does not change

- **No `TRACE_SCHEMA_VERSION` bump.** A new optional field with a default does not change the
  meaning of an existing payload. A trace written before this has no spend recorded — which is
  *true of it*: it was not recorded, so its replay understates exactly as much as it did before.
  The version is for a format change replay cannot absorb; this is one it absorbs.
- **A timed-out call reports zero.** The worker thread is not killable, so whatever it went on to
  spend is discarded with its result ([0007](0007-in-process-tool-timeouts.md)). An unreported
  spend is better than a guessed one.
- **The charge is checked even when it is zero**, so the budget is now re-checked at every tool
  call rather than only at every model call. Deliberate, and pinned by
  `test_a_charge_of_nothing_is_still_a_check`: a bound that is sometimes not checked is sometimes
  not a bound, and "this call was free" is not a reason to let an exhausted clock stand.
- **The sweep fixture is still not promoted.** The mechanism is now correct, but a fixture has to
  come from a real model run (`tests/test_recorded_runs.py` refuses a stub), and that is the next
  step rather than this one.

## Consequences

- `Spend` is the vocabulary for consumption; `Budget` stays the vocabulary for bounds. A call has
  the first and never the second.
- Any future tool that spends reports it the same way, and neither the loop nor the budget learns
  its name.
- The parent's `cost_usd` and `tokens_total` are now a *sum of records*, which is what makes them
  replayable. A number that no record explains is a number replay cannot restore.
