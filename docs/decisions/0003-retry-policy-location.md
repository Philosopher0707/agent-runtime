# 0003 — Retry policy lives in the tool boundary, not the loop

Date: 2026-09-27
Status: accepted

## Context

Two components could plausibly own "retry once if idempotent". The taxonomy states the
rule, but not where it is implemented. It matters, because the placement determines
whether replay is exact.

## Decision

**`tools/registry.py` owns how many times to try. `runtime/loop.py` owns whether the run
can continue.**

`dispatch` performs the whole attempt sequence for one logical call — the initial attempt,
one repair for malformed output, one retry for a transient error — and returns a single
`ToolCallRecord` carrying `attempts` and `attempt_outcomes`. The loop reads the outcome
and decides: continue, degrade, or stop as partial.

## Consequences

- A recorded call replays as its recorded outcome sequence. The policy is not re-run, so
  a replay cannot take a different path from the trace that recorded it.
- `MAX_ATTEMPTS` bounds one logical call at three attempts regardless of what the tool
  does, so no single call can consume the run.
- The registry is not "tool implementations" — it is the tool *contract*, which is what
  the boundary table already assigns it.

## Alternatives considered

**Retry in the loop.** Rejected: the loop would have to know a tool's idempotency class
and would re-run that decision during replay, which is precisely the failure mode replay
exists to eliminate.
