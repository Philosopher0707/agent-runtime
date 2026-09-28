# 0022 — Context summarisation is truncation, and its marker is load-bearing

Date: 2026-09-28
Status: accepted

## Context

The context-management half of the runtime — summarisation, dropping, the soft/hard threshold
split — had only ever run against synthetic test input. Measured across every trace the project
had produced: **256 context events, zero firings**, because the largest prompt ever assembled was
~2,300 tokens against an 8,000-token soft threshold.

The first real content large enough to fire it produced a clearer answer than expected.

## What the mechanism actually is

`context/assembler.py::summarise` does not summarise. It collapses whitespace and keeps the
**first `summary_chars` characters** (300 in `configs/triage.yaml`):

```python
collapsed = " ".join(text.split())
if len(collapsed) <= max_chars:
    return f"[summarised] {collapsed}"
return f"[summarised] {collapsed[:max_chars]}..."
```

That is deliberate — a model-written summary is not deterministic, and replay requires
determinism — but the name promises more than the mechanism delivers. It is the same defect class
as `confirmation_token` ([decisions/0020](0020-auth-is-the-deployers-boundary.md)), and it is
recorded here rather than silently fixed, because the *behaviour* turned out to depend on it.

There are in fact **two** front-truncations, and both leave a visible marker:

| where | bound | marker |
|---|---|---|
| the untrusted envelope | `untrusted_max_chars` (8,000) | `[... N characters omitted by the runtime ...]` |
| context summarisation | `summary_chars` (300) | `[summarised] … …` |

## What happened when it first fired

The test input was built so the fact that matters is the **first** thing lost: a long routine
thread with a cross-account data exposure at character ~4,500 of ~7,300 — inside the envelope
bound, far past the summary window, and read first, while summarisation works oldest-first.

The run:

```
step 1: read 010, 011, 012, 013, 014
step 2: summarisation fires → the model RE-READS 010
step 3: summarisation fires again → the model re-reads 011 and 014
step 4: escalate(010) — correct, with a correct reason
```

It got the right answer, and the summary it produced said *"buried in a long routine thread"* —
it knew the fact had been buried.

## Decision

**The marker is a contract with the model, and it stays.** Specifically:

1. **Truncation stays deterministic.** A model-written summary would make the prompt depend on a
   second model call, and replay would stop being exact. This is the trade, and it is taken
   knowingly.
2. **The marker stays visible and must not be softened to save tokens.** It is the only signal
   that information was lost, and the recovery above happened *because* the model read it as "you
   had this and I took it away". Removing it would make the loss silent, which is the one outcome
   this design must not produce.
3. **The runtime does not verify recovery.** It cannot tell whether the answer accounted for what
   was cut. The model is the safety net and the **step budget is the backstop** — the run above
   used 6 of 8 steps and 8 reads for 5 messages, and a larger queue would hit the bound rather
   than loop forever.

## Consequences

- `evals/live_cases/11-triage-survives-context-summarisation.yaml` pins the behaviour, and
  `evals/fixtures/triage-survives-summarisation.jsonl` pins it in CI with no key and no network.
- The **naming is a known wart.** `summarise` truncates, and `summarise_above_tokens` names a
  threshold for something else. Renaming touches a config key every configuration sets, so it is
  recorded rather than done — but the next person to read `assembler.py` should not have to
  discover it from the body.
- The case **cannot assert that summarisation fired.** The live-case vocabulary has no way to say
  it; that is visible in the trace, not in the case. If the summarisation path were refactored
  away, the case would still pass while testing something weaker. Named here because a test that
  silently stops testing its own subject is the failure this project keeps finding.

## The trigger to revisit

**A run where the model answers from the truncated text without re-reading.** That would mean the
marker is not doing its job, and the response is a louder marker or a first-and-last window —
not a quieter one.

## Alternatives considered

**Summarise with a model.** Rejected: it breaks replay determinism, and it puts a second model
call in the hot path of every context assembly. Recorded in
[decisions/0009](0009-trust-model.md) as the same reason a judge does not belong there.

**Keep the whole tool result and let the hard ceiling drop turns.** Rejected: dropping loses more
than truncating, and the taxonomy requires the soft threshold to be tried first.

**Rename the marker to say what happened** (`[truncated: first 300 characters kept]`). Tempting
and probably right, but it is a change to a prompt-visible string on the evidence of one run. Held
until a run shows the current wording failing.

## Addendum, 2026-09-28 — the case was asserting two things, and one of them is variable

`triage-survives-context-summarisation` originally asserted `escalate` as well as the judgement.
On a later run it failed: the model read everything, named `010`, set `needs_human: true` — and
did not escalate.

**The judgement survived summarisation, which is what this case is for.** The *action* is a
separate property that this domain's model only sometimes takes — the same split recorded in
L74, where the model's judgement was right and its compliance with the action was the unreliable
half. So the case now asserts the judgement, and `triage-escalates-a-real-problem` covers the
action. One case, one claim.

Worth stating plainly, because it is a **domain reliability finding and not a test detail**: a
triage agent that identifies a cross-account data exposure and does not escalate it is the failure
this domain exists to prevent, and it has now happened twice in five runs. Nothing in the runtime
can detect it — the output says `needs_human: true` and the run reports `ok`, because the runtime
does not know that a judgement implies an action. That gap is recorded, not fixed; closing it
would mean the runtime reasoning about the domain's semantics, which is exactly what the one rule
forbids.
