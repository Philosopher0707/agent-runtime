# 0008 — Structured output validates a JSON Schema subset

Date: 2026-09-27
Status: accepted

## Context

A configuration may require its final answer to be JSON conforming to a schema
(`output.format: json` plus `output.schema`). That needs validation, and the question is
how much of JSON Schema to implement.

## Decision

**Implement a documented subset, and declare it.**

`runtime/structured.py` enforces exactly these keywords, exported as `SUPPORTED_KEYWORDS`:

```
type · required · properties · items · enum · additionalProperties
```

Anything else is ignored — and the module says so, in a constant, with a test asserting
the set, and a second test asserting each advertised keyword is actually enforced.

## Consequences

- No dependency, and no pretence of completeness.
- A schema using `$ref`, `allOf`, `patternProperties`, or `format` is **not** enforced.
  The failure mode is a permissive validator, which is why the limitation is declared
  rather than left implicit.
- A schema can be written against the subset and be fully enforced, which is what every
  worked example does.
- Type checking is JSON-correct rather than Python-correct: `true` is not an integer.

## Alternatives considered

**Add `jsonschema`.** The honest choice once schemas grow past this subset. Rejected for
v1 because the runtime's own schemas are small and the dependency buys coverage nothing
currently uses.

## Upgrade path

Swap `parse_structured`'s validation step for a library call. The seam is one function;
the call sites and the tests do not change. The trigger is the first schema that needs
`$ref` or `allOf`.
