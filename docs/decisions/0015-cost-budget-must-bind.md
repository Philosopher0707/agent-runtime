# 0015 — A cost bound a configuration cannot honour is a configuration error

Date: 2026-09-27
Status: accepted

## Context

`max_cost_usd` is required and `gt=0`, so every configuration *claims* a positive cost bound
and there is no way to opt out by setting it to zero.

Cost is computed per response from `provider.price_input_per_mtok` and
`price_output_per_mtok`, which **default to `0.0`**. So an `openai_compat` configuration that
omits prices reports `$0.000000` for every run while spending real money, and the bound it
declares can never fire.

Nothing warned. The budget was working perfectly; the *input* to it was a silent zero.

## Decision

**A configuration whose cost bound cannot trip is refused at load.**

The check is scoped to providers that actually cost money: `openai_compat`. A stub has no
spend, so a zero price is correct there, and the three worked examples stay valid.

The refusal names the way out, in the same spirit as `api_key_env`:

> `budget.max_cost_usd is 0.25 but both provider prices are 0.0, so every run reports $0.00
> and this bound can never trip. Set provider.price_input_per_mtok and
> provider.price_output_per_mtok to the endpoint's real rates — the bound is only as real as
> those two numbers.`

Either price being non-zero is enough to make the bound real, so a partial configuration is
accepted rather than second-guessed.

## Consequences

- **A bound that cannot fire is worse than no bound**, because it reports safety it does not
  provide. That is the principle; this is the enforcement.
- It found a real misconfiguration immediately: two of the project's own tests built an
  `openai_compat` configuration without prices while testing something else. They were
  constructing invalid configurations and passing.
- Prices are per-configuration, not a table. There is still no per-model lookup, no versioned
  price table, and no policy for an unknown model — those remain open, and this decision does
  not pretend to close them. It closes the *silent* case, which is the dangerous one.

## Alternatives considered

**Default the prices to something non-zero.** Rejected: inventing a rate produces a bound that
fires at the wrong point, which is a quieter lie than a bound that never fires.

**Warn instead of refusing.** Rejected: a warning in a log is exactly the kind of thing that
is not read until after the invoice.

**A versioned price table keyed by model, with an explicit unknown-model policy.** The fuller
answer, and deliberately not this decision. It needs a source of truth for prices, a
staleness policy, and a decision about what to do when the model is not in the table. Worth
doing when there is a reason; refusing the silent case is worth doing now.
