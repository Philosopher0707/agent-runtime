# 0012 — PII: what may enter context, and what must be redacted before logging

Date: 2026-09-27
Status: accepted

## Context

The spec's safety section says, in one line:

> PII: state what may enter context and what must be redacted before logging.

Those are two different boundaries, and the sentence asks for the first to be *stated* and
the second to be *done*. Until now neither was written down, and the trace recorded
everything verbatim — a tool returning a customer record put that record on disk, in a file
that outlives the prompt, is read by people who were not party to the run, and gets copied
into bug reports.

## Decision

**1. The context is not redacted. This is the statement the spec asks for.**

| Content | Enters the prompt? | Control |
|---|---|---|
| The task | Yes, verbatim | The principal chooses what to send. `max_input_chars` bounds it. |
| Tool output | Yes, verbatim, inside the envelope | The *tool set* a configuration chooses. `untrusted_max_chars` bounds it. |
| The system prompt | Yes | The configuration. |
| The final answer | Produced, then policed | The disclosure guardrail. |

Redacting the prompt would **silently change the task**: a runtime that quietly answers a
different question than the one it was asked is worse than one that declines. The controls
on what enters context are the tool set and the input bound — not a text filter.

**2. The trace is redacted, at one chokepoint.**

`TraceWriter.emit` is the only place any event is written, so the policy is applied there
and no event kind can be added later and quietly bypass it. Redaction covers strings
nested anywhere in a payload, **keys included** — a tool that returns a mapping keyed by an
email address is not hypothetical.

**3. Redaction is off by default, because it and replay are mutually exclusive.**

Replay rebuilds each prompt from the trace and compares its hash against the recorded one.
A redacted trace cannot reproduce the prompt it recorded, so **a redacted trace is not
replayable**. `runtime/replay.py` refuses one with a distinct exception,
`ReplayUnavailable`, and says what to do about it.

That is the trade, stated rather than hidden: durability of the record, or the ability to
reproduce the run. You do not get both, and a design that claimed both would mean one of
them silently not working.

**4. The patterns are deliberately aggressive.**

The failure economics here are the **opposite** of the injection guardrail's:

| | injection markers | redaction patterns |
|---|---|---|
| a false positive costs | a refused run | a digit missing from a log |
| a false negative costs | a payload reaching the model | personal data on disk |
| therefore | must be precise | must over-match |

So `phone` requires a `+`, parentheses, or 3-3-4 grouping — broad enough to catch real
numbers, shaped specifically so it does **not** eat an ISO date. A redactor that consumed
every timestamp would make a trace useless for the one thing traces are for.

The default set is `email`, `phone`, `credit_card`, `national_id`, `api_key`. A
configuration may name a subset; an unknown name is a load-time error.

**5. Redaction is visible in the trace.**

A reader must be able to tell what they are looking at. The policy is in the `run_started`
configuration block, and a `redaction` event after `run_finished` records the counts per
pattern. The summary is tied to the run *finishing*, not to the file closing, so a completed
run always has it.

## Consequences

- Redaction cannot be enabled after the first event; a half-redacted trace is worse than an
  unredacted one, because the difference is invisible on inspection.
- `configs/` gains no new worked example: redaction is a guardrail option, not a capability.
  It is covered by eval case 33 instead, which asserts both that the run is unaffected and
  that the data is absent from the serialised trace.
- The eval check reads the **whole** trace rather than the summary event, so a leak in an
  event the summary does not describe still fails.
- Pattern-based redaction is not a guarantee. It catches shapes, not semantics: a name in
  prose, or a phone number in a format not listed, passes through. That is why the default
  is off and the decision is explicit rather than automatic.

## Alternatives considered

**Redact the prompt too.** Rejected: it silently changes the task. If a deployment must not
let certain data reach a model, the answer is not to send it — a tool set and a
configuration, not a filter that alters the question.

**Redact but keep replay working, by hashing before redacting.** Considered, and rejected
as dishonest: the recorded prompt hash would then describe text the trace does not contain,
so a reader could verify nothing with it. A hash that cannot be checked against its own
trace is a decoration.

**Encrypt the trace instead of redacting it.** A real option, and out of scope: it moves the
problem to key management without answering "what must not be in the record".

**Use a PII-detection model rather than patterns.** Would catch semantics that patterns
miss. Rejected for now: a model in the logging path is a second thing to be wrong, and the
failure mode of *this* component is the safe direction (over-redaction). Worth revisiting if
the pattern set proves too coarse in practice.
