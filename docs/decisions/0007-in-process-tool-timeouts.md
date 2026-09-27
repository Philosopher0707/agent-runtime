# 0007 — In-process tool timeouts leak a thread

Date: 2026-09-27
Status: accepted

## Context

Tools are in-process Python functions (the default tool protocol). Python cannot kill a
thread. A tool that ignores its deadline therefore cannot be forcibly stopped.

## Decision

**Run each invocation on a thread pool and enforce the timeout with
`future.result(timeout=...)`.** On expiry the record is `timeout`, the future is
cancelled, the result is discarded, and the run proceeds. The worker thread is left to
finish on its own.

## Consequences

- The *contract* is enforced: the loop is never blocked past the deadline, the run always
  terminates, and the tool's late result is never used. That is what the taxonomy row
  requires.
- A pathological tool leaks a thread until it returns. With a bounded pool this can
  eventually starve the executor. The mitigation is a well-behaved tool, and the real fix
  is a different tool protocol.
- The registry's executor is closed with `cancel_futures=True` at the end of a run, so
  queued work is dropped even though running work is not.

## Alternatives considered

**Run each tool in a subprocess.** Correct, and it would make the timeout real. Rejected
for v1 because it forces every tool to be picklable and adds process management to a
runtime whose default tool protocol is explicitly "in-process Python functions". This is
the documented upgrade path when tools must run untrusted or in another language.

**`signal.alarm`.** Rejected: it only works on the main thread, and it would make the
runtime unsafe to embed.

## Test coverage

`test_a_tool_that_overruns_its_timeout_is_timed_out` forces the executor path with a tool
that sleeps past its own deadline. It leaves one thread running for ~0.3s, which is the
limitation this decision records.
