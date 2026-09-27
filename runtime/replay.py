"""Replaying a recorded run. The trace is the only input.

Replay is **safe by construction**: it never executes a tool, and it never even needs a
tool implementation. The tool layer is replaced by a dispatcher that serves recorded
records and recorded descriptors, so replaying a trace of a mutating tool cannot mutate
anything a second time — and replaying a trace whose tools no longer exist still works.

Descriptors come from the trace rather than from the catalogue because they are part of
every prompt: they are sent to the model on each call, so their text is inside every
prompt hash. Rebuilding them would make a replay depend on the catalogue being
unchanged, and would diverge silently for any run assembled some other way.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path

from providers.replay import ReplayDivergence, ReplayProvider
from runtime.loop import run
from runtime.schemas import RunOutput, ToolCallRecord, ToolCallRequest, ToolDescriptor, TraceRecord
from runtime.trace import (
    TraceWriter,
    model_call_records,
    new_trace_id,
    read_trace,
    recorded_config,
    recorded_descriptors,
    recorded_task,
    tool_call_records,
)

#: Where a replayed run's own trace is written when the caller does not say.
DEFAULT_REPLAY_TRACE_DIR = ".traces/replay"


class ReplayUnavailable(ReplayDivergence):
    """The trace cannot be replayed, because redaction removed what replay needs.

    A distinct type rather than a bare divergence: this is a *policy*, not a bug. A
    divergence means the code changed; this means the trace was deliberately made
    unfaithful. Reporting them the same way would send someone hunting for a defect that
    is not there.
    """


class RecordedDispatcher:
    """Serves recorded tool records instead of running tools.

    It verifies that each request matches the recorded one — same step, same tool,
    same arguments — so a divergence in the *tool* path is caught as loudly as one in
    the model path.
    """

    def __init__(
        self,
        *,
        descriptors: Sequence[ToolDescriptor],
        records: Sequence[ToolCallRecord],
    ) -> None:
        self._descriptors = list(descriptors)
        self._records = list(records)
        self._index = 0

    def descriptors(self) -> list[ToolDescriptor]:
        return list(self._descriptors)

    def dispatch(
        self,
        request: ToolCallRequest,
        *,
        step: int,
        confirmation_token: str | None = None,
    ) -> ToolCallRecord:
        if self._index >= len(self._records):
            raise ReplayDivergence(
                f"replay exhausted after {len(self._records)} recorded tool call(s); "
                f"the run asked for {request.name!r} at step {step}"
            )
        record = self._records[self._index]
        if (
            record.step != step
            or record.name != request.name
            or record.arguments != request.arguments
        ):
            raise ReplayDivergence(
                f"tool call mismatch: replay asked for {request.name!r} at step {step} with "
                f"{request.arguments!r}, but the trace recorded {record.name!r} at step "
                f"{record.step} with {record.arguments!r}"
            )
        self._index += 1
        return record

    def close(self) -> None:
        """Nothing to release: no executor is created, because nothing is ever run."""


def replay(
    trace_path: str | Path,
    *,
    trace_dir: str | Path = DEFAULT_REPLAY_TRACE_DIR,
    clock: Callable[[], float] = time.monotonic,
) -> RunOutput:
    """Re-run a recorded run from its trace alone.

    ``clock`` is injectable because a wall-clock-bounded run only replays identically
    under a deterministic clock. Everything else replays under any clock.
    """
    return replay_trace(read_trace(trace_path), trace_dir=trace_dir, clock=clock)


def replay_trace(
    trace: TraceRecord,
    *,
    trace_dir: str | Path = DEFAULT_REPLAY_TRACE_DIR,
    clock: Callable[[], float] = time.monotonic,
) -> RunOutput:
    """Replay an already-parsed trace."""
    config = recorded_config(trace)
    redaction = config.guardrails.redaction
    if redaction.mode != "off":
        raise ReplayUnavailable(
            f"trace {trace.trace_id} was written with redaction mode {redaction.mode!r}, so it "
            f"cannot be replayed: replay rebuilds each prompt from the trace and compares its "
            f"hash against the recorded one, and redaction removed the text that would match. "
            f"This is the trade documented in runtime/redact.py, not a defect. Re-run the task "
            f"with redaction off if you need a replayable trace."
        )

    dispatcher = RecordedDispatcher(
        descriptors=recorded_descriptors(trace),
        records=tool_call_records(trace),
    )
    provider = ReplayProvider(model_call_records(trace))

    with TraceWriter(trace_dir, new_trace_id()) as tracer:
        return run(
            recorded_task(trace),
            config=config,
            provider=provider,
            tools=dispatcher,
            tracer=tracer,
            clock=clock,
        )


__all__ = [
    "DEFAULT_REPLAY_TRACE_DIR",
    "RecordedDispatcher",
    "ReplayDivergence",
    "ReplayUnavailable",
    "replay",
    "replay_trace",
]
