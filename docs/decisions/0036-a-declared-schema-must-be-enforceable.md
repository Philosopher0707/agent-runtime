# 0036 — A declared schema must be enforceable

Date: 2026-09-29
Status: accepted

## Context

`runtime/structured.py` implements a documented JSON Schema **subset** — `type`, `required`,
`properties`, `items`, `enum`, `additionalProperties` — and declares it in a constant,
`SUPPORTED_KEYWORDS`. Grep the repository and that constant appears **only in tests**. Nothing in the
runtime ever read it.

So a configuration could declare any schema at all, and the validator would do this with it:

```
parse_structured("{}", {"$ref": "#/definitions/missing"})  -> ok=True
parse_structured("{}", ["not", "a", "schema"])             -> ok=True
```

`ok=True`. A `$ref` is ignored, a schema that is not even an object is ignored, and the run reports
that its output contract was kept. **A configuration declaring `$ref` believes its answers are
constrained and they are not** — which is
[0015](0015-cost-budget-must-bind.md)'s defect in a different currency: a declaration that cannot do
what it says, reporting success anyway.

Ignoring an unsupported keyword is a reasonable thing for a subset validator to do. Ignoring it
*silently* while the configuration believes the check happened is not.

## What was built

`unsupported_keywords(schema)` in `runtime/structured.py`, and a `model_validator` on `Configuration`
that refuses at load. The constant is load-bearing now.

**Three details, each of which made the first version useless**, and each of which is a test:

- **Property names are not keywords.** The keys inside `properties` *name* the properties; the values
  are schemas. A walk that does not know that reported `claim`, `verdict` and `evidence` as
  unsupported — in a schema entirely inside the subset — which would have refused every real
  configuration in this repository.
- **`additionalProperties` may be a boolean.** JSON Schema allows it and the validator handles it, so
  a `bool` where a schema is expected is not an error.
- **Anything else where a schema is expected is refused**, not walked past: `items: [{...}, {...}]` is
  tuple validation, outside the subset, and would otherwise be silently ignored.

## Refused, not warned about

A warning on a configuration is read once; a refusal is read every time. This is the same call 0015
made, and for the same reason: the whole point is that the declaration and the enforcement cannot
disagree without someone being told.

It also makes **adding a keyword a deliberate act**. [0008](0008-structured-output-subset.md) named
the trigger for the upgrade path — *the first schema that genuinely needs `$ref`* — and until now
nothing would have announced that the trigger had been pulled. Now it does, by refusing the schema.

## Evidence

Every schema in the repository was checked before the refusal was added: four, all inside the subset.
One mutation — the keyword walk returning nothing — is caught by
`test_a_schema_outside_the_validator_subset_is_refused`.

**And the refusal found two tests using a schema that is not a schema.** `test_replay.py` checked
that the repair note is order-independent with `{"b": {...}, "a": {...}}` — bare keys with no
`properties` wrapper. Every key would be ignored and the validator would accept any answer, so the
test was exercising a contract that did not exist. Rewritten with a real schema, in two key orders,
which is what the test always meant.

## What this does not do

- **It does not extend the validator.** The subset is the same six keywords; only its enforcement
  changed.
- **It does not reject a schema for being *wrong*, only for being unenforceable.** A schema that is
  valid and inside the subset but badly written is the author's business.
