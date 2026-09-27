# 0001 — The run protocol: a text transcript and one control tool

Date: 2026-09-27
Status: accepted

## Context

The runtime has to talk to models that support native tool calling and models that do
not, without the loop learning which is which. It also has to represent "the model wants
to ask a clarifying question" in a way that can be counted and bounded.

## Decision

**The transcript is plain messages** — `system`, `user`, `assistant` — and tool calls are
rendered in-band as text inside the assistant turn (`[called calculator(expression='1+1')]`).
Tool results travel as `user`-role messages wrapped in the untrusted envelope.

**`ask_clarification` is a control tool.** It is registered for every configuration, and
the loop intercepts it before dispatch. It is never executed.

## Consequences

- No vendor-specific `tool_call_id` bookkeeping, and the protocol works against a model
  with no native tool-calling at all.
- The provider still receives the native tool schema, so a well-behaved model emits
  native tool calls; only the *transcript* rendering is generic.
- Tool results are visibly data in the transcript, which is where the untrusted envelope
  belongs: with the data it governs.
- The loop knows exactly one tool name. That is a deliberate exception to "the loop knows
  no tool", justified because clarification is protocol rather than capability.

## Alternatives considered

**Native tool-call protocol throughout.** Rejected: it requires per-vendor message shapes
inside the loop, which is the provider-specific code the loop is forbidden to contain.

**Detect clarification requests by pattern-matching prose.** Rejected: it cannot be
counted reliably, and "exactly one clarifying question" would become a hope.
