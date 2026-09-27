# 0005 — Token accounting is a heuristic

Date: 2026-09-27
Status: accepted

## Context

The budget must enforce a token bound, and the context assembler must decide when to
summarise. Both need a token count before the model call, which means before any
provider-reported usage exists.

Accurate counting means a tokeniser per provider. That is a dependency per provider, and
it makes the bound mean something different for each one.

## Decision

**Estimate tokens as `characters / chars_per_token`** (default 4), with the ratio
configurable per configuration in `context.chars_per_token`.

The estimate is used for two things only: deciding when to summarise or drop context, and
*bounding* a run. The authoritative token count for accounting comes from the provider's
own `usage` block, which is what `Budget.add_usage` charges.

## Consequences

- Identical enforcement across every provider, including ones whose tokeniser we do not
  have.
- The estimate is conservative for English prose and less so for dense non-Latin scripts.
  A configuration dealing with those should lower `chars_per_token`.
- Because the *estimate* governs context and the *reported usage* governs the budget,
  a wrong estimate cannot make the budget wrong — it can only make truncation happen
  earlier or later than ideal.

## Alternatives considered

**`tiktoken` per provider.** Rejected for v1: a dependency and a provider-specific
concept inside the runtime, for a number that only needs to be roughly right.

**Provider-reported usage only.** Rejected: usage arrives *after* the call, which is too
late to decide what to put in the prompt.

## Upgrade path

Inject a real tokeniser behind the same `estimate_tokens` seam. The call sites do not
change; only the function does.
