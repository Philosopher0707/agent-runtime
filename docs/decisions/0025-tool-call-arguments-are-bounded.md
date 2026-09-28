# 0025 — A turn's tool-call arguments are bounded, because nothing bounded them

Date: 2026-09-28
Status: accepted

## Context

[Decisions/0024](0024-dropping-takes-the-request-with-it.md) fixed a symptom and named its cause:
the hard ceiling was dropping requests away from their own results, and the reason it had to was
that **the request is the large thing**.

A tool *result* is bounded by `guardrails.untrusted_max_chars`. **Nothing bounded what the model
asked for.** The assistant turn renders every call's arguments in full, so sixteen calls with
8,000-character arguments added **~65,000 tokens in a single step** — which the soft threshold
cannot touch, because summarisation only rewrites tool *results*, and which the hard ceiling could
answer only by removing the whole turn.

## Decision

**`context.max_call_chars` bounds the rendered tool calls a single turn may contribute.** Default
4,000.

Three parts:

- **It bounds the turn, not each call.** Sixteen individually-bounded calls still add up. The thing
  that must not happen is one turn dominating the prompt, so the budget is the turn's.
- **The loss is signalled**, with the same convention as the untrusted envelope:
  `[... N characters of tool call arguments omitted by the runtime ...]`. A silent cut is the one
  outcome this design keeps refusing to have — the model has to be able to tell that its own
  record is partial, or it will treat it as whole.
- **It changes nothing about what the tool receives.** The bound is on the *transcript's
  rendering*, not on the call. `dispatch` still gets the real arguments.

## Why a configuration value

Every other bound in this area already is one — `max_prompt_tokens`, `summarise_above_tokens`,
`summary_chars`, `untrusted_max_chars`. A constant would have been simpler and inconsistent, and a
capability that cannot tune how much of its own transcript it keeps is a capability with a hidden
policy.

## Consequences

- **The ceiling is no longer forced by a single step.** The scenario that broke 0024 now fits: the
  run ends on `budget_exhausted:max_tokens_total` — a real budget bound — rather than on a
  structural failure it could not avoid.
- **No committed fixture's prompt changed**, so replay is unaffected. Checked rather than assumed:
  the largest rendered arguments in any fixture are **415 characters** against a 4,000-character
  bound. All twenty-eight recorded-run tests pass unchanged.
- Four tests in `tests/test_context.py`, including a guard that an ordinary turn is untouched and
  one that the bound is on the turn rather than on each call.
- `_bound` is a small shared helper, so the omission marker has one definition.

## Alternatives considered

**Reuse `untrusted_max_chars` for the arguments.** Rejected on meaning: that bound exists because
tool output is untrusted, and the model's own request is not untrusted — it is merely large.
Sharing the number would have coupled two unrelated policies and made either one hard to change.

**Bound each call rather than the turn.** Rejected: it does not bound the thing that broke. Sixteen
calls at the bound is still sixteen times too much.

**Do nothing, since the ceiling now handles it.** Rejected: that is what 0024 fixed the symptom of.
The ceiling handling it means the model loses its own request, which is exactly the incoherence
0024 was written to stop.
