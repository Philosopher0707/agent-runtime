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

## Measured against a real endpoint, 2026-09-27

The ratio above was chosen on intuition and never checked. It has now been checked against
OpenRouter, and it was wrong by **5.5x to 12.6x**.

| task | estimated | actual | ratio |
|---|---|---|---|
| "Say OK." | 52 | 657 | 12.6x |
| a prose question | 65 | 666 | 10.2x |
| one tool call | 195 | 1416 | 7.3x |
| two tool calls | 277 | 1511 | 5.5x |

Two corrections, both accepted:

1. **The tool schemas are counted.** They are sent as the request's `tools` field on every
   call and billed as prompt tokens; the estimate counted only `messages`. For a
   three-tool configuration that is roughly 620 tokens of fixed overhead that no threshold
   could see.
2. **They are counted at their own ratio.** `chars_per_token` stays **4.0** for prose, which
   the measurement supports (prose came in at about 4.6 characters per token, so 4.0 is
   conservatively low — the safe direction). JSON schemas measured at **2.0 characters per
   token**, because JSON is punctuation and short repeated keys, and a new
   `schema_chars_per_token` covers them.

After both corrections the estimate is close on the shapes measured: 662 against 657 (0.8%
over) and 1422 against 1422 (exact).

**Also settled here: the two thresholds measure different things.** The soft threshold
(`summarise_above_tokens`) compares against the *transcript*, because that is the part that
grows. The hard ceiling (`max_prompt_tokens`) compares against the *whole request*, because
that is what the model has to fit. They had both been compared against the same total, so a
large fixed overhead summarised a three-token tool result to make room for a schema that
never changes.

The upgrade path above is unchanged, and the measurement strengthens rather than weakens the
case for it — but a two-ratio heuristic gets within a percent on the shapes measured, which
is enough for thresholds whose job is to trigger early rather than to be exact.

**What this cost to find:** four real API calls. Every test had used the same wrong estimate
on both sides, so every test agreed with itself, and no amount of testing against a stub
would ever have surfaced it.
