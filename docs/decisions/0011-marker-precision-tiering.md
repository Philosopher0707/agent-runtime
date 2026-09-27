# 0011 — Marker precision: two tiers, and proximity for corroboration

Date: 2026-09-27
Status: accepted

Supersedes the marker list in
[0006](0006-untrusted-content-policy.md), which remains the record of the *policy*
(fail-closed, envelope-first). This decision is about the *detector*.

## Context

[0006](0006-untrusted-content-policy.md) chose fail-closed: a marker in tool output refuses
the run. That makes the marker list's precision a user-facing property — every false
positive is a legitimate run that did not happen — and the list had been written from
intuition, with no measurement behind it.

So it was measured. `evals/markers.py` holds a corpus in three buckets — benign tool
output, injection-shaped benign content, and hostile payloads — and
`scripts/measure_markers.py` grades a rule against it. The first measurement:

| | before |
|---|---|
| false positives on benign tool output | **9 / 25 = 36.0%** |
| recall on unambiguous payloads | 13 / 15 = 86.7% |
| files in this repository a file-reading tool would refuse | **10 / 46** |

The dominant cause was the bare phrase `system prompt`, which appears in any codebase that
has one: `AGENTS.md`, `README.md`, `runtime/loop.py`, and `runtime/schemas.py` all tripped
on it. Two payloads were also *missed* (`disregard the earlier guidance`, `forget all prior
commands`) because the override pattern did not cover those verbs and nouns.

A detector that refuses one legitimate run in three is not a guardrail; it is an outage.

## Decision

**Two tiers, and corroboration must be adjacent.**

- **`DIRECTIVE`** — asserts something *about the reader*: an instruction aimed at the
  model. One is enough. These are specific by construction.
- **`CONTEXT`** — only indicates the *shape* of an instruction: a role label, a modal
  verb, a mention of a system prompt. **Two distinct ones are required, and they must
  appear within `CONTEXT_WINDOW_CHARS` (400) of each other.**

Proximity is the load-bearing part of the second tier. An injected instruction is a *local
burst* — a fake transcript turn, a planted paragraph. Two generic phrases a page apart are
a coincidence; the first version of the corroboration rule still refused `AGENTS.md`,
`context/sanitize.py`, and `docs/decisions/0009` on exactly that coincidence.

**Every marker must be load-bearing.** `tests/test_marker_precision.py` ablates each marker
and fails if removing it costs no recall. A marker that catches nothing is a
false-positive generator with no upside, which is how the original list reached 36%.

**The false-positive budget is zero, and the corpus must exercise each rule.** Both halves
were learned the hard way while writing this:

- A budget of *5%* on a 26-sample corpus tolerates exactly one false positive, so the
  threshold never bit — raising `CONTEXT_WINDOW_CHARS` to infinity passed the suite. A
  budget is only a tripwire if the corpus is large enough to make it sharp.
- Proximity was covered by a unit test but not by the corpus, so the metric would not have
  noticed its removal. A benign sample now exists specifically for that shape: a long
  deployment guide mentioning "system prompt" in the introduction and "You must run" 676
  characters later. Tiering alone does not save it; only adjacency does.

Zero is the right budget because every benign sample was chosen by hand to represent output
a real tool returns. There is no rate of refusing those that is acceptable. The risk this
creates is worth naming: the cheapest way to make a failing suite pass is to delete the
sample that fails. Deleting a benign sample is the wrong fix.

## Result

Same corpus, same exclusions, both rules:

| | before | after |
|---|---|---|
| benign false positives | 9 / 25 = 36.0% | **0 / 25 = 0.0%** |
| recall (unambiguous) | 13 / 15 = 86.7% | **15 / 15 = 100.0%** |
| repo files refused | 10 / 46 | **2 / 46** |

Recall improved as well as precision, because the override pattern was broadened to cover
`forget`/`override`/`bypass` and `commands`/`guidance`/`policy` while the *tiering* removed
the need to treat generic phrases as evidence.

The two remaining repo hits are both explained and both left in place:

- `docs/decisions/0009-trust-model.md` quotes a payload verbatim to illustrate the trust
  model. Quoted payloads are indistinguishable from payloads by pattern — the documented
  limit.
- `context/sanitize.py` contains a comment reading *"a repository's own spec mentions
  "system prompt" and "you must run" hundreds of characters apart"*, which places both
  phrases seven characters apart. **The comment explaining the false positive is itself a
  false positive.** It is left as written: rewording it would improve the metric without
  changing the limit.

## Consequences

- **The scan is a tripwire, not a filter.** It catches blatant cases; the envelope is what
  does the real work. Stated in the module docstring so it cannot be mistaken for coverage.
- **Marker-less payloads are still missed**, and that is asserted rather than hoped:
  `test_payloads_without_marker_phrases_are_missed_and_that_is_documented` fails if the
  guardrail ever starts catching them, so the documentation gets updated with it.
- **The corpus is a permanent tripwire.** `make markers` prints the report and exits
  non-zero below threshold; the same thresholds are enforced in tests, so the report and
  the tripwire cannot disagree. Adding a marker now requires evidence that it earns its
  place.
- The corpus is small and hand-built. It is far better evidence than intuition, and it is
  not a substitute for real tool output — see the open questions in `AGENTS_LEARNING.md`.

## Alternatives considered

**Annotate-and-continue instead of refusing.** Still rejected, for the reason in 0006:
deciding whether the model *complied* with an injected instruction needs a judge in the
hot path. The measurement did, however, expose a follow-up worth deciding: whether a trip
should refuse the *run* or only *withhold the tool result*. Refusing the whole run is the
coarsest possible response to a lexical signal. Recorded as the first open question in
`AGENTS_LEARNING.md` rather than decided here.

**Drop the `CONTEXT` tier entirely.** Nearly done, on the strength of an ablation showing it
caught nothing. Testing that finding first — by adding three payloads that only the context
tier catches, each a real attack shape — showed the tier is load-bearing for a fake
transcript turn and a fake "your instructions have been updated" notice. The ablation
measured the *corpus*, not the markers; the fix was to strengthen the corpus.

**Raise the corroboration count to three.** Crude: it weakens detection without addressing
why generic phrases co-occur, which is distance.

## Correction, same day

The claim above — *"the report and the tripwire cannot disagree"* — was **not true** when it
was written. The ablation was enforced only by the test. With a dead marker added,
`make markers` printed its report and exited **0**; only `make ci` noticed, because `check`
runs the ablation test.

It was found by testing the gate rather than the code: adding a marker that catches nothing
and asking whether the gate that exists to catch that actually catches it.

The ablation now lives in `scripts/measure_markers.py`, is printed in the report, and is
called by the test — so the sentence is true now rather than aspirational. This is the same
pattern as L5 in `AGENTS_LEARNING.md`: a consistency claim between two things has to be
checked, not asserted.
