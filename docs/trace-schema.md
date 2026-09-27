# The trace schema

One file per run, `{trace_id}.jsonl`, under the trace directory (`.traces/` by default). It is
the only record, and **replay reconstructs a run from it alone** — so this document describes a
contract, not a log format.

Written from `runtime/trace.py`. If the two disagree, the code is right and this file is a bug —
`tests/test_trace_schema_doc.py` fails when they drift.

## The envelope

One JSON object per line, keys sorted, UTF-8, flushed after every line.

| field | type | meaning |
|---|---|---|
| `schema_version` | int | Which format this line is written in. See [decisions/0014](decisions/0014-trace-schema-version.md). |
| `ts` | string | Wall-clock timestamp of the write. Telemetry only — replay ignores it. |
| `trace_id` | string | Same on every line. A file whose lines disagree is refused. |
| `event` | string | One of the events below. |
| `payload` | object | Event-specific. Schemaless on purpose: an old trace's payload is not validated against today's model. |

`sort_keys=True` is why the file is diffable and why a re-emitted line is byte-identical.
`ensure_ascii=False` keeps non-ASCII readable rather than escaped.

## The events

| event | payload | emitted |
|---|---|---|
| `run_started` | `task`, `config_name`, `config`, `provider`, `model`, `tools`, `prompt_fingerprint`, `revision` | once, first |
| `context` | `ContextRecord`: `step`, `message_count`, `estimated_tokens`, `summarised_results`, `dropped_messages`, `system_prompt_present`, `task_present` | once per step, before the model call |
| `model_call` | `ModelCallRecord`: `step`, `provider`, `model`, `prompt_hash`, `prompt_tokens`, `completion_tokens`, `latency_s`, `cost_usd`, `finish_reason`, `refusal`, `response` | once per model call |
| `tool_call` | `ToolCallRecord`: `step`, `name`, `arguments`, `outcome`, `attempts`, `attempt_outcomes`, `duration_s`, `result`, `error`, `guardrail`, `confirmation_applied` | once per dispatch |
| `failure` | `FailureEvent`: `failure_class`, `status`, `detail`, `step`, `guardrail` | zero or more |
| `run_finished` | `RunOutput` — the same payload `POST /run` returns | once, last |
| `redaction` | `mode`, `patterns`, and per-pattern counts | only when redaction is on |

Two of these carry more than they look like they do:

- **`run_started.tools` holds the tool *descriptors*, not their names.** They are sent on every
  request, so their text is inside every `prompt_hash`. Rebuilding them from the catalogue would
  make replay depend on the catalogue being unchanged — and would silently diverge for any run
  whose tool set was assembled another way.
- **`tool_call.attempt_outcomes` is a sequence, not a count.** `attempts: 2` with
  `attempt_outcomes: ["timeout", "ok"]` is what lets a replay reproduce a retry rather than
  re-decide it. Retry policy lives in the tool boundary
  ([decisions/0003](decisions/0003-retry-policy-location.md)), and the trace records its
  *outcome*, never its reasoning.

## The invariants

- **Append-only.** Lines are never rewritten, and the file is never edited.
- **One writer per file.** `TraceWriter` holds the handle; two writers on one trace is a bug.
- **A trailing partial line is tolerated.** A process killed mid-write leaves one, and
  `read_trace` drops it rather than refusing the whole record — the trace matters most exactly
  when something went wrong. A malformed line anywhere *else* is an error, not a skip.
- **Redaction happens at `emit`, and nowhere else.** One chokepoint, so no event kind can be
  added that bypasses it. A redacted trace cannot be replayed
  ([decisions/0012](decisions/0012-pii-context-and-redaction.md)).
- **A version this build does not know is refused by name**, not reported as a divergence
  ([decisions/0014](decisions/0014-trace-schema-version.md)).

## What replay needs, and therefore what may not be dropped

Replay re-validates the recorded configuration, re-validates the recorded descriptors,
recomputes every `prompt_hash`, and serves each tool call from its recorded
`attempt_outcomes` — **it never executes a tool**. Anything that would change one of those
values changes what the trace means, which is what the schema version exists to signal.

## Known gaps

- **The `redaction` event records what was removed, not what was there.** The policy is visible
  either way, because `run_started.config` carries the redaction mode.
- **`ts` is wall-clock and not reproducible.** Replay takes an injectable clock for exactly this
  reason; the recorded timestamps are read, not recomputed.
- **Nothing prunes the trace store.** `make eval` wipes its own working directory
  (`.traces/evals/`) at the start of each run, so eval traces are transient by design. Run
  traces under `.traces/` accumulate, and `run_started.revision` is what lets you sort them by
  build.
