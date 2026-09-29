# 0033 — The log is a boundary

Date: 2026-09-29
Status: accepted

## Context

Every dependency of the loop was a protocol — `Provider`, `ToolBoundary` — except one. `run()` was
typed `tracer: TraceWriter`, so the module that runs a task imported the module that records it.
That is the same defect as a loop that names a tool, one layer over: **a core that names its own
recorder has an opinion about how runs are recorded**, and this runtime's whole claim is that it
does not.

The obligations were also unwritten, and they are not small. Three of them cannot be seen by
reading either file:

* a tool-call record that is **dropped, duplicated or reordered** — the run still succeeds and the
  replay fails later, somewhere else;
* a record written **after** its result was used — a trace that omits something the model acted on,
  which is a record that lies;
* a log that **cannot write** and lets the run continue, leaving a gap nothing reports.

## What was built

`RunLog`, declared in `runtime/loop.py` beside `ToolBoundary` — the two things the loop needs from
the world outside itself. `TraceWriter` satisfies it **structurally**; it does not inherit from it,
and the loop does not import it. That is the measurable outcome:

```
runtime/loop.py   no longer imports runtime.trace
```

The four clauses, each pinned by a test that fails if the clause is broken:

1. **One record per dispatch, exactly once, in dispatch order.**
   `test_the_loop_records_one_tool_call_per_dispatch_in_order` counts them through a log that keeps
   everything in memory — so the claim is checked where it is caused, not inferred from a file.
2. **The record is written before its result is used.**
   `test_the_tool_call_is_recorded_before_it_is_interpreted` makes the causal order observable: a
   failing optional tool produces a `failure` event *because* of its record, so if the record were
   written after interpretation the failure would come first.
3. **A log that cannot write stops the run.**
   `test_a_log_that_cannot_write_stops_the_run_before_the_result_is_used` — the exception is not
   caught, and the model is never asked again.
4. **The log is a sink, not a participant.**
   `test_the_log_is_a_sink_not_a_participant` — the same task through two different logs produces
   the same `RunOutput`. That is what makes a disagreement between a run and its replay a fact about
   the code or the trace, and never about the logger.

Plus two tripwires on growth, both read from the AST:

* `test_the_loop_calls_nothing_the_boundary_does_not_declare` — a new `self.tracer.<event>()` added
  without adding it to `RunLog` fails, so the boundary cannot fall behind the code it describes.
* `test_the_loop_does_not_import_the_concrete_writer` — with a non-vacuity guard, since the absence
  of an import proves nothing unless something imports it.

## The one place the loop raises

`run()` promises never to raise for a task-level problem. A log that cannot write is not a
task-level problem, and the correct behaviour is to raise — because a run with no record is not a
run this runtime is willing to perform. There is no status to report, since there is no record to
report it in.

This is the fail-safe direction that matters here: the safe outcome is **no run**, not a run with a
silent gap. Continuing would produce exactly the artefact this project refuses everywhere else — a
record that looks complete and is short.

## Evidence

Five mutations, each restored from a byte copy with the hash verified:

| mutation | caught by |
|---|---|
| a new event the boundary does not declare | `test_the_loop_calls_nothing_the_boundary_does_not_declare` |
| a logging failure swallowed | `test_a_log_that_cannot_write_stops_the_run_before_the_result_is_used` |
| an event removed from the boundary | `test_the_loop_calls_nothing_the_boundary_does_not_declare` |
| the record written after interpretation | `test_the_tool_call_is_recorded_before_it_is_interpreted` |
| the writer imported again | `test_the_loop_does_not_import_the_concrete_writer` |

**The probe's self-test was scoped wrong first, and said so.** It removed a `run_finished` call and
reported `BLIND` — because that mutation is caught by `test_trace.py`, which this probe does not
run. The harness was right and the guard was fine; the self-test was measuring the wrong file. It
now uses a mutation the boundary file must own.

## What this does not do

- **No new failure class.** A log that cannot write raises; it does not produce a status. The
  taxonomy is unchanged, and `test_every_failure_class_has_a_test` did not need touching.
- **No second sink.** A separate tool-call log file would be a second copy of a fact the trace
  already holds, and would need a rule about which is authoritative. The trace remains the record.
- **`TraceWriter` is unchanged.** It already satisfied the boundary; the boundary was the part that
  was missing.
