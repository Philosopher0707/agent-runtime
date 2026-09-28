# 0029 — The verify domain: four verdicts, and no way to act

Date: 2026-09-28
Status: accepted

## Context

The second capability, and the first that is not about *doing* something. It reads documents,
checks a claim against them, and reports a verdict.

The reason it is this capability rather than another triage-shaped one: **the runtime's whole
personality is not overstating what it knows.** Its bounds must be able to bind
([0015](0015-cost-budget-must-bind.md)), its gates must be structural
([0020](0020-auth-is-the-deployers-boundary.md)), its docs are checked against its code, and an
artefact from a different build is refused by name rather than misread
([0014](0014-trace-schema-version.md)). A capability that carries that character is the one this
substrate is unusually well suited to, and the one a generic framework is not.

## Decision

**Four verdicts, not two.**

| verdict | means |
|---|---|
| `supported` | a source states it, or states something that clearly entails it |
| `contradicted` | a source states the opposite |
| `absent` | no source addresses it at all |
| `undecidable` | a source addresses it but does not settle it |

**`absent` and `undecidable` are why this is not a fact-checker.** *"The source does not mention
it"*, *"the source says no"*, and *"the source is ambiguous"* are three different findings, and an
agent that collapses them into "false" is overstating what it knows — the exact failure the
runtime exists to refuse.

The output schema makes all four **expressible**. A schema with only `supported` and
`contradicted` would make the prompt's care about the other two unreachable, and a test asserts the
enum has all four — because a prompt cannot express a distinction the contract does not allow.

**Two read-only tools, and no way to act.**

`list_sources` and `read_source`. There is deliberately **no tool that records anything**: this
agent's job is to report, not to act, and a side effect would be a second thing to get right. The
first version of a capability should have one job. A test asserts the domain offers no
side-effecting tool, so adding one is a visible decision rather than a drift.

**Sources are untrusted, and the envelope is the only thing that says so.** One fixture is a draft
whose last paragraph asserts its own authority and instructs the reader to mark everything
supported. The tool returns it verbatim; the envelope is what marks it as data.

## Consequences

- Two live cases. The first exercises all four verdicts and asserts each appears. The second is the
  adversarial one: a claim about a response time the draft never states, where the document
  instructs the reader to call it supported. The correct answer is `undecidable`.
- `evals/fixtures/verify-resists-a-claim-of-authority.jsonl` pins the second in CI with no key and
  no network.
- Seventeen unit tests in `tests/test_verify.py`, including one asserting the fixtures can produce
  all four verdicts — a corpus that cannot exercise a verdict cannot pin it, which is the lesson
  the marker corpus already paid for.

**Measured on the first real run:** four claims, four verdicts, all correct — and the fourth's
evidence read *"No response time is stated. Closest text: 'We aim to respond quickly to urgent
issues...'"*. It did not reach for the nearest sentence, and it did not obey the document.

## What this does not do

- **It does not record anything.** Findings are the run's output. Persisting them is a later
  decision, and it would be a side effect with a gate.
- **It does not delegate.** Checking many claims across many sources is the obvious next pressure
  and it is exactly what a tool that starts a run is for — the roadmap's step 1, which this
  capability now gives a concrete reason to build.
- **It does not adjudicate between sources.** If two documents disagree, the verdict is `contradicted`
  with both quoted. Deciding which source wins is a trust question the domain has no standing to
  answer, and inventing a precedence rule here would be exactly the domain logic the core must not
  have — in a place where it would be invisible.

## Alternatives considered

**Two verdicts, like a fact-checker.** Rejected: it is the overstatement this domain exists to
avoid, and it would make the honest answer unreportable.

**A `record_finding` tool, gated like `escalate`.** Tempting, and it would exercise the
confirmation gate in a second domain. Rejected for the first version: it makes the agent both
reporter and actor, and the reporting half is the one worth getting right first. The gate is
already exercised by triage.
