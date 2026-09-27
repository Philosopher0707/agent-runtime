# 0014 — The trace is versioned, per line

Date: 2026-09-27
Status: accepted

## Context

`TraceEvent` was `(ts, trace_id, event, payload)` with no version. Replay re-validates the
recorded configuration, re-validates the recorded tool descriptors, and recomputes each
prompt hash — so a change to the trace *format* and a genuine defect in the *code* produced
the same error:

```
ReplayDivergence: context assembly is not deterministic
```

That is the wrong diagnosis, and it sends someone hunting for a bug that is not there. An
old trace is a **fact about the file**; a divergence is a **claim about the code**.

## Decision

**Every line of a trace carries `schema_version`.**

`read_trace` refuses a version it does not know, naming both numbers:

> `line 1 is trace schema version 0, and this build speaks version 1. Replay cannot absorb a
> format change — re-run the task on this build, or check out the build that wrote this trace.`

Three details are load-bearing:

- **Per line, not in a header.** A header can be absent, or present while the body was
  written by something else. Per-line versioning means a mixed file cannot pass as a whole
  one, and that is checkable.
- **Version 1 is the format as it stands**, which is why the field has a default rather than
  being required. Every trace written before the field existed is genuinely version 1, not
  unknown. A default here is honest; the alternative — making it required — would refuse
  every existing trace for no reason.
- **The version describes the *meaning* of a payload, not the set of events.** Adding a new
  optional event does not need a bump; renaming a field, changing a shape, or removing an
  event does. Otherwise the number moves for changes replay can absorb, and a version that
  moves for everything distinguishes nothing.

## Consequences

- A format change is a fact rather than a misdiagnosis. That is the whole point.
- A bump is a deliberate act with a written reason, which is what makes it worth reading.
- Replay of an old trace now fails *early and clearly* instead of failing late and
  misleadingly.
- This is the precondition for any format work later — including projecting traces into
  another vocabulary. You cannot migrate a format you cannot name.

## Alternatives considered

**A `trace_started` header event.** Rejected: a file whose header says one thing and whose
body says another would pass.

**Semantic versioning (major.minor.patch).** Rejected as over-engineering. There is one
consumer — replay — and it needs *equality*, not ordering. `1` and `2` are sufficient until
something needs to reason about compatibility rather than identity.

**No version, and a better error message.** Rejected: the message can only be good if it
knows what it is comparing against.
