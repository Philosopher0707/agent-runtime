# 0017 — OpenTelemetry GenAI is a projection, not the substrate

Date: 2026-09-27
Status: accepted

## Context

The trace format is bespoke: `TraceEvent(ts, trace_id, event, payload)`. OpenTelemetry's
GenAI semantic conventions define a standard vocabulary for exactly this domain — span names,
`gen_ai.*` attributes, token usage, tool execution, agent and workflow spans — and adopting
them would give the record interoperability it does not have.

Investigated 2026-09-27. The conventions now live in their own repository
(`open-telemetry/semantic-conventions-genai`), authored as YAML and generated into docs by
Weaver, covering model spans, agent spans, events, metrics, exceptions, MCP, and
provider-specific refinements.

**Their state is the governing fact: everything is `Development`.** The only Stable attributes
they lean on are the non-`gen_ai.*` ones (`error.type`, `server.address`, `server.port`). The
schema URL in the repository is literally `TODO`.

## Decision

**The vocabulary belongs in a projection. The local record stays the substrate.**

When a projection is built it will be a pure function from a stored trace to spans, living in
an adapter — a consumer of the record, exactly as a provider adapter is a producer. It will
not be the storage format, and it will not be in the loop.

**Why it cannot be the substrate:** telemetry is allowed to **sample and drop**, and replay is
not. That is not a gap in the spec, it is what telemetry is for. Layered on it, the content
attributes (`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.definitions`) are
opt-in, filterable, truncatable, and carry explicit warnings that they may contain personal
data. Replay needs the opposite of all four.

And there are things replay needs that the convention has no place for at all — they are this
runtime's semantics, not observations:

| replay needs | in the convention? |
|---|---|
| `attempts` — how many times a call was tried | no |
| `attempt_outcomes` — `[timeout, ok]` | no |
| the retry policy's decision, recorded | no |
| `prompt_hash` — what makes replay a checked fact | no |

## The mapping, for when it is built

| this record | semconv | verdict |
|---|---|---|
| a run | `invoke_agent` (`INTERNAL` — the loop is in-process) | adopt |
| a step's model call | `chat {gen_ai.request.model}` | adopt |
| a tool dispatch | `execute_tool` | adopt |
| token usage | `gen_ai.usage.input_tokens` / `.output_tokens` | adopt; **cost has no standard attribute** |
| `FailureClass` | `error.type` (Stable) + a local `failure_class` | adopt `error.type`, keep the taxonomy |
| tool descriptors | `gen_ai.tool.definitions` | adopt the name, but **mandatory here, not opt-in** |
| `prompt_hash`, `attempts`, `attempt_outcomes` | — | keep local |
| the envelope, injection markers, redaction | — | keep local; runtime policy, not telemetry |

One thing worth stealing before any of it: the convention has a **pointer mode** for content —
store it elsewhere and record `gen_ai.content.url` with `gen_ai.content.upload_status`. That is
a third answer to the PII problem alongside redaction, and possibly a better one: don't redact,
relocate. Revisit when [decisions/0012](0012-pii-context-and-redaction.md) is next touched.

## Consequences

- Adopting the vocabulary later is cheap, because a projection is regenerable: when the
  convention renames something (it will), re-run the projection over history rather than
  migrating a storage format.
- The local format is not coupled to a spec that renames things daily. A rename would be a
  one-file change.
- If a projection is never built, nothing is lost — the local record was never waiting on it.
- **Trigger to build:** the first real observability need (a backend to export to, or more
  runs than a person can read), or the conventions going Stable. Not interest.

## What this decision does not claim

The `execute_tool` span's attribute list was **not retrieved verbatim** — the documentation
fetch truncated at the same point three times. The span's existence and operation name are
confirmed (`gen_ai.execute_tool.client`); its required and recommended attributes are not.
Anyone acting on the mapping table above should read that section of the spec first rather
than trusting this table's tool rows.

## Alternatives considered

**Make the semconv the trace format.** Rejected on the sampling mismatch above, and because it
would tie a replay guarantee to a `Development` vocabulary whose schema URL is `TODO`.

**Adopt the attribute names in the local format now.** Rejected, and this reverses an earlier
proposal of mine. If the stored fields are named after an unstable spec, a rename becomes a
data migration. Keeping the vocabulary in the projection makes a rename a one-file change and
moves no stored trace.

**Do nothing and revisit later.** Rejected only because the analysis was done and would
otherwise be re-derived. The decision is to *not* adopt the substrate, which is worth writing
down precisely so nobody adopts it by accident.
