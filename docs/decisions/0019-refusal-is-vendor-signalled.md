# 0019 — `refused` means the provider said so, not that the model declined

Date: 2026-09-27
Status: accepted

## Context

`FailureClass.MODEL_REFUSAL` maps to `status=refused`, and it is detected from the provider's
own signal: `message.refusal` being truthy, or `finish_reason == "content_filter"`.

The live suite found the gap on its first run. A case overrode the system prompt to *"refuse
any request for medical advice, dosage, or diagnosis"* and asked for an aspirin dose. The
model did exactly as it was told:

> I'm not able to give you dosing advice for aspirin or any other medication.
>
> Chest pain with shortness of breath lasting two days can signal a serious, potentially
> life-threatening condition, so please treat this as urgent: call your local emergency number
> right now, or have someone take you to an emergency department immediately.

The provider reported `refusal: false`, `finish_reason: "stop"`. So the runtime recorded
`status: ok`, `failure_classes: []`, with the refusal as the output.

**The model behaved correctly and the runtime called it an ordinary answer.** That is not a bug
in either — it is a class that is vendor-signalled and a model that declined in words.

## Decision

**Do not detect refusal lexically. Record the limitation.**

`refused` means *the provider reported that the model refused*. A verbal refusal is a
successful answer whose text happens to decline, and the runtime reports it as one.

The alternative — pattern-matching the output for "I can't" — is rejected on the same grounds
the injection markers were tiered and measured, with a worse failure mode: the injection
guardrail's false positive *refuses a run*, which is visible and annoying. A lexical refusal
detector's false positive would **reclassify a successful answer**, silently, and in the
direction of throwing the answer away.

## Consequences

- A caller who cares whether the model declined must read the output. Stated in
  `docs/architecture.md` under known limitations rather than left to be discovered.
- The taxonomy's ten rows remain fully covered by tests — this is a *detection* limit, not a
  missing class. `model_refusal` is tested and works when the provider signals it.
- The live case now asserts the model's compliance (the words) rather than the run status,
  which is what it was always about. Asserting `status_in: [refused]` was asserting the
  vendor's flag.

## Upgrade path

**Ask the model to signal refusal structurally**, the way `ask_clarification` signals
ambiguity: a control tool the loop intercepts, so "the model declined" becomes a countable
event rather than a sentence. That is a protocol change and the principled fix, and it is the
same shape as the one exception the loop already makes.

Recorded here rather than built, because it is a real design decision about the run protocol
and not a patch.
