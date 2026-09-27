# 0016 — The prompt has a name; a registry comes later

Date: 2026-09-27
Status: accepted

## Context

`prompt_hash` is the authority on **whether** a prompt changed, and replay verifies it. It
cannot say **what** changed, because four things are inside it and they live in four files —
two of them Python source:

| component | lives in |
|---|---|
| the system prompt | `configs/<name>.yaml` |
| the tool schemas | `tools/`, via their descriptors |
| the untrusted envelope | `context/sanitize.py` |
| the tool-call renderer | `context/assembler.py` |

Measured: one character added to any of the four moves the same single hash. So "the hash
differs" is all a person gets, and the question they actually ask — *which part moved?* — has
no answer in the record.

A proposal was made to add a prompt registry: immutable versions, movable tags (`stable`,
`candidate`), semantic versions with defined bump rules, and canary splits. It is a good
proposal, and it does not work until the sentence above stops being true. **You cannot
version a composition you cannot name.**

## Decision

**Name the parts now. Build the registry when there is a second prompt or a second person.**

Implemented: `context/fingerprint.py` hashes each stable component separately and combines
them into an **identity** — a hash of the stable parts, which is what a version number would
name. It is recorded in `run_started` alongside the configuration and the descriptors.

Three properties make it worth having:

- **The per-step `prompt_hash` is untouched.** It remains the authority on whether the prompt
  changed; replay depends on it, and adding a diagnosis must not move it. Asserted by a test.
- **The identity excludes the transcript.** A prompt version must not move because a tool
  returned a different number. Nothing about the task or the transcript is an input.
- **`changed_from` names the difference.** The diagnosis, not just the fact.

## What a registry would still have to get right

Recorded here so the analysis is not re-derived. Four things, and two of them are corrections
to the original proposal:

1. **Derive the MAJOR bump where it is derivable.** "Breaking output schema change" is
   checkable — diff `output.schema` mechanically. The MINOR/PATCH boundary is genuine
   judgement, so it stays a claim, but a *recorded* one, next to the diff and the previous
   hash. A version number a human assigns by judgement is the same defect this project has hit
   twice: a hard-coded fact that nothing checks.
2. **The hash is the authority on whether; the version is a human's interpretation of what
   kind.** Conflating them produces a version that disagrees with its own hash.
3. **The registry must be an index over the trace, never a competing record.** The trace
   already records the configuration, the descriptors and the prompt hashes. The first time
   the two disagree, there would be two authorities and no rule for which wins.
4. **Canary splits are a deployment feature with no deployment.** A canary needs traffic to
   split, a metric to compare, and volume for the comparison to mean anything — and it fights
   determinism, because "which prompt did the eval grade?" becomes a question. Skip it until
   there is real traffic.

## Consequences

- A prompt change now reports *which* component moved. That is most of the human benefit, for
  a fraction of the cost of a registry.
- **Prompt rollback would become a tag move**, which is better than
  [decisions/0013](0013-rollback-scope.md)'s `git revert` for the common case: a tag move
  reverts exactly the prompt, where a revert undoes everything in a commit, and it needs no
  commit or CI run at all. When a registry is built, 0013 should be revisited for the prompt
  case specifically.
- The trigger to build the rest is the second prompt or the second person. Until then the eval
  suite is the change-control mechanism — a prompt change that breaks a case fails CI. It
  works; it is simply not human-readable.

## Alternatives considered

**Do nothing.** Rejected: `prompt_hash` is correct and unhelpful, and the diagnosis gap is
real today, not hypothetically.

**Version the configuration file's contents.** Rejected: it would version one of four inputs
and disagree with the hash — a registry whose identity is not the hash is a registry that
lies about what ran.

**Build the full registry now.** Rejected as premature rather than wrong. It is Phase 2 work
that earns its keep with multiple prompts and multiple people, and building it first would
have meant building it on a prompt that had no name.
