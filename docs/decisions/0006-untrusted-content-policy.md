# 0006 — Untrusted content policy is fail-closed

Date: 2026-09-27
Status: accepted

## Context

Tool output can contain anything, including text that reads as an instruction. The
taxonomy requires a guardrail trip to produce `status=refused` with the guardrail named,
but it does not say how aggressively to trip.

Two broad options:

1. **Fail-closed** — detecting instruction-like content in tool output refuses the run.
2. **Annotate-and-continue** — wrap the content, note the markers, and let the model
   proceed.

## Decision

**Fail-closed.** Tool output is always wrapped in the untrusted envelope, and always
scanned. If any marker fires, the run is refused, the marker names are recorded, and the
tool the payload asked for is *not* called.

The wrapper is applied unconditionally and independently of the scan, so a bug in the
scan cannot mean unwrapped data.

## Consequences

- **False positives are accepted.** A tool that returns the phrase "system prompt" in
  innocent prose refuses the run. The mitigation is a tool author's job — return clean
  data — not a reason to weaken the check.
- The check is cheap, deterministic, and needs no second model, so it cannot itself be
  talked out of its decision.
- The eval case is non-vacuous: the scripted model *would* have called a registered
  side-effecting tool on the next step.

## Alternatives considered

**Annotate-and-continue.** Rejected for v1: deciding whether the model *complied* with an
injected instruction requires a judge in the hot path, which is a second thing to be
wrong and a non-deterministic one. A softer policy is a deliberate future change, not an
accident.

**Scan only content from "external" tools.** Rejected: "tool output is untrusted, no
exception, including for our own tools" is explicit, and a trust distinction between our
tools and others is exactly the sort of exception that becomes a hole.

## What this does not cover

Semantic injection that contains no marker phrase. A payload written as ordinary
plausible prose passes the scan. This is a known limitation of a lexical guardrail; the
envelope is what does the real work, and the scan is a second line rather than the first.
