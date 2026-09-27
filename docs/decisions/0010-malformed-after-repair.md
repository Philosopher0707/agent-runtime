# 0010 — Malformed-after-repair keeps its class, and behaves as a tool error

Date: 2026-09-27
Status: accepted

## Context

The taxonomy row reads:

> Tool returns malformed output — One repair attempt, then **treat as tool error**. Never
> pass malformed data downstream.

Two readings are available for "treat as tool error": rename the outcome to `tool_error`,
or handle it the way a tool error is handled.

## Decision

**Keep the class `tool_malformed`; apply the tool-error *behaviour*.**

After one failed repair the record's outcome is `malformed`, the class recorded is
`TOOL_MALFORMED`, and the escalation is identical to a tool error: optional → continue
degraded, required → partial. The malformed result is never placed in the context.

## Consequences

- Triage keeps the more specific fact. "The tool returned the wrong shape twice" and "the
  tool raised" are different problems with different fixes, and collapsing them loses
  that.
- Behaviour is indistinguishable from a tool error, which is what the row asks for.
- The row's operative requirement — *never pass malformed data downstream* — is enforced
  structurally: `_attempt` returns `result=None` on a malformed outcome, so there is no
  value available to pass.

## Alternatives considered

**Relabel as `tool_error`.** Rejected: it discards information the operator needs, and the
behavioural requirement is already met without it.

## Test coverage

`test_tool_malformed_after_repair_is_not_passed_downstream` forces the row and asserts
`result is None`, so the structural guarantee is checked rather than assumed.
