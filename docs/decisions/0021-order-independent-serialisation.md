# 0021 — Config-derived data entering a prompt serialises order-independently

Date: 2026-09-28
Status: accepted

## Context

The first real domain's first live run ended in `ReplayDivergence`:

```
step 4: the replayed prompt does not match the recorded one
(rebuilt 4b19a9d9..., recorded 15f3fe61...). Context assembly is not deterministic.
```

That message names the wrong suspect. Context assembly *is* deterministic — replaying twice
rebuilt the identical hash both times, and steps 1–3 matched exactly. Something had **changed**
between the recording and the replay, and only at step 4.

The run had taken the **repair pass**: the model answered `"category": "bug_report"`, which is
not in the enum, so the loop added a note explaining the parse error and asked again. Step 4 is
the prompt containing that note.

`_repair_note` built it with `json.dumps(config.output.schema_)` — **without `sort_keys`**. And
a configuration arrives by two different doors with two different key orders:

| door | key order |
|---|---|
| parsed from `configs/*.yaml` | insertion order — what the file says |
| read back out of a trace | **alphabetical**, because `TraceWriter.emit` writes every line with `sort_keys=True` |

So the original run serialised the schema as `{"type": ..., "required": ..., "properties": ...}`
and the replay serialised the same schema as `{"additionalProperties": ..., "properties": ...}`.
Different strings, different prompt hash, divergence.

**Every run that used the repair pass was unreplayable**, and had been since the repair pass was
written.

## Decision

**Anything derived from a configuration and placed in a prompt must serialise the same way
whichever door the configuration came through.** Concretely: `sort_keys=True`.

Enforced by a test rather than by care — a blanket rule over `runtime/`, because there are only
two `json.dumps` call sites and both want sorting anyway, and because the failure is invisible
until something replays.

## Why it survived

Two reasons, and the second is the interesting one:

1. **Nothing replayed the repair path.** `make_config()` is text-output, so structured output
   never engaged in the replay tests. The branch was simply untravelled.
2. **The repair pass is the least-travelled path in the loop** — it only runs when a model
   produces an *almost*-valid answer. The stub never does, because a stub is a specification of
   a model and a specification does not make mistakes.

A real model does. The first real domain hit it on its first live run, which is exactly what
phase 2 is for.

## Consequences

- **A trace recorded by the buggy code can never replay.** The fix makes future runs replayable;
  it cannot retroactively change what was written. This is the same principle as
  [decisions/0014](0014-trace-schema-version.md): an old trace is a fact about the old code, so
  re-record rather than migrate. The affected live trace was discarded, and a fresh run — which
  replays identically — was promoted as the fixture instead.
- `tests/test_replay.py` gained three tests: a run that uses the repair pass and replays exactly,
  a guard that the repair pass actually ran in that test, and a unit test that the same schema in
  two key orders produces one note.
- The `sort_keys` rule is now checked for every `json.dumps` in `runtime/`.

## Alternatives considered

**Sort the schema when the configuration is loaded.** Rejected: it treats the symptom. The
configuration is not the problem — *serialising it in arrival order* is, and the same bug would
reappear the next time a config-derived value reaches a prompt.

**Make `_repair_note` take the schema as a pre-serialised string.** Rejected as more machinery
than the rule needs, and it would hide the ordering question rather than answer it.

**Leave it and document that repair-using runs do not replay.** Rejected: replay is the project's
strongest claim, and a documented hole in it is worse than a one-word fix.
