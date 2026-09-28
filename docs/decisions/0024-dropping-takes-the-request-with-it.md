# 0024 — Dropping removes a request with its results, and never the last one

Date: 2026-09-28
Status: accepted

## Context

The hard ceiling (`max_prompt_tokens`) and the `ContextUnfit` refusal had never fired either —
measured across every trace, `dropped_messages` was zero everywhere. Summarisation had just been
driven for the first time ([decisions/0022](0022-summarisation-is-truncation.md)); this is the
other half of the same path.

Driven with a configuration whose every step is full of maximum-size tool results, it fires — and
what it did was wrong.

## What was wrong

The loop dropped **single turns, oldest first**:

```python
while total() > hard and turns:
    turns.pop(0)
```

The prompt the model received, measured:

```
[0] system    'You are a test assistant...'
[1] user      'go'
[2..17] user  '[summarised] <<<UNTRUSTED_TOOL_OUTPUT tool=echo ...'  × 16
```

**Every assistant turn was gone.** Sixteen answers to questions the model could not see it had
asked, and a transcript that begins with a tool result. The run reported `ok`.

The cause is that the *assistant* turn is the large one: it carries the tool-call **arguments**, so
sixteen calls with 8,000-character arguments is ~65,000 tokens — while `untrusted_max_chars` bounds
the *results* and nothing bounds the arguments or the assistant turn. Summarisation cannot help,
because it only rewrites tool results.

## Decision

**Drop the oldest *group* — a request and the results it produced — and never the last one.**

Three parts, each with a reason:

1. **A request and its results are one unit of meaning.** Dropping half of it produces a
   transcript whose leading turn is an answer to a question that is no longer there.
2. **The most recent group is never dropped.** It is the one thing the model is mid-conversation
   with, and it is the difference between a degraded transcript and no transcript.
3. **If what must be kept does not fit, raise `ContextUnfit`.** The refusal now covers both ends —
   the protected pair (the system prompt and the task), and the most recent request with its
   results. The run reports `context_overflow` / `partial`.

Part 3 exists because **part 1 alone was worse, and that was measured rather than guessed**:
pairing the drop on a single 65,000-token group emptied the transcript entirely, leaving the model
nothing at all. A transcript it cannot parse is bad; an empty one is not better.

## Consequences

- Three tests in `tests/test_context.py`, including the invariant pairing buys — *after the
  protected pair, the transcript never begins with a tool result* — and one that a group too large
  to fit raises rather than sending half of it.
- `ContextUnfit`'s message said "system prompt + task need ~N tokens", which is now wrong when the
  cause is the kept group. It says "what must be kept" instead.
- The run above now reports `partial` / `context_overflow` instead of `ok`.

## The gap this exposed and did not close

**Tool-call arguments are unbounded in the transcript, and nothing summarises the assistant turn.**

`untrusted_max_chars` bounds what a tool *returns*. Nothing bounds what the model *asks for*, and
the request is rendered into the transcript in full — so a single step can add 65,000 tokens of
argument text that the soft threshold cannot touch and the hard ceiling can only drop.

That is the real fix and it is not this decision: the transcript's rendering of a call should be
bounded the way its result is, without changing what the tool receives. Recorded here because the
symptom (an incoherent transcript) and the cause (an unbounded request) are different things, and
fixing the symptom is what this decision does.

**Closed the same day, by [decisions/0025](0025-tool-call-arguments-are-bounded.md).** The gap
lasted one commit, which is the intended shape of "fix the symptom, name the cause, then fix the
cause" — and it is worth noting that the cause was only *findable* because the symptom had been
made honest first. A transcript that lies about what happened hides the reason it is lying.

## Alternatives considered

**Drop single turns, as before.** Rejected: it is the bug.

**Pair the drop and allow an empty transcript.** Rejected on measurement — it is what the first
attempt did, and losing everything is not an improvement on losing the structure.

**Bound the tool-call arguments here.** Tempting, and it is the actual fix — but it changes what
appears in every prompt, which is a prompt-identity question
([decisions/0016](0016-prompt-identity.md)) and deserves its own change with its own evidence.
