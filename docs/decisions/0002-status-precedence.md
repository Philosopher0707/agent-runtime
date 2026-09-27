# 0002 — Status precedence

Date: 2026-09-27
Status: accepted

## Context

A single run can go wrong in more than one way. A tool can time out, and then the model
can refuse. Something has to decide which single status the run reports, and the choice
must not hide the other thing.

## Decision

**Failure classes are recorded; statuses are derived.**

Every observed failure becomes a `FailureEvent` carrying both a `failure_class` and the
`status` it claims. The run's status is the most severe claim, by this order:

```
refused > failed > partial > degraded > ok
```

All individual claims survive in `RunOutput.failures`, and the deduplicated set of
classes survives in `RunOutput.failure_classes`. Nothing is collapsed away.

## Consequences

- A tool that errors and then succeeds on retry is a recorded `tool_error` with a status
  claim of `ok`. The class is visible; the status is honest.
- A guardrail trip after a degraded tool call reports `refused`, and the degradation is
  still in the record.
- `refused` outranking `failed` is deliberate: a refusal is a *deliberate* terminal
  outcome and the most specific claim available about the run.

## Alternatives considered

**One status, no classes.** Rejected: it cannot express "this recovered", and the
recovery is exactly the thing worth knowing.

**First failure wins.** Rejected: order-dependent, so the same run could report
different statuses depending on scheduling.
