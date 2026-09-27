# 0004 — `provider_error` added to the failure taxonomy

Date: 2026-09-27
Status: accepted

## Context

The initial taxonomy has nine rows. None of them covers "the model endpoint could not be
reached", or "the endpoint answered with something that is not a completion". Those are
real, common, and not the same as any existing row:

- not `tool_error` — no tool was involved
- not `unparseable_output` — the *final answer* was never reached
- not `model_refusal` — the model did not decline; it never spoke

Without a row, the loop would either raise (turning a routine network failure into a
crash) or mislabel it.

## Decision

**Add `provider_error`, mapping to `status=failed`.**

Adapters raise `ProviderError`. The loop catches it, records the class, and ends the run.
An adapter never decides a run status; it only reports that it could not produce a turn.

The class is also used, with a *degraded* status claim, for a provider turn that carries
neither text nor a tool call — the endpoint responded but with nothing usable.

## Consequences

- A network failure is a run outcome, not an exception.
- A call that did not complete is not counted as a model call. `model_calls` stays
  truthful: it counts turns the model actually produced.
- The taxonomy is now ten rows, and the coverage test
  (`test_every_failure_class_has_a_test`) enforces that a row cannot be added without a
  test that forces it.

## Alternatives considered

**Let the exception propagate.** Rejected: a runtime whose value is a disciplined failure
taxonomy should not turn a routine upstream failure into a crash.

**Reuse `unparseable_output`.** Rejected: it would blame the model for a network fault and
make the class useless for triage.
