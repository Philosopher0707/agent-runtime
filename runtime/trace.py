"""The only writer of the run record. Append-only, and replayable.

A trace alone has to reconstruct the run. That is why every model call carries the
response it returned and every tool call carries the sequence of outcomes it
produced: those are the only two things the loop cannot recompute.

Append-only is enforced by construction — the file is opened ``"a"``, one JSON
object per line, flushed after each event. Nothing in this module can rewrite or
truncate an existing line.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.config import Configuration
from runtime.schemas import (
    ContextRecord,
    FailureEvent,
    ModelCallRecord,
    RunOutput,
    ToolCallRecord,
    ToolDescriptor,
    TraceEvent,
    TraceRecord,
)


class TraceError(Exception):
    """A trace could not be read back as a trace."""


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


class TraceWriter:
    """One file per run, one event per line, ``trace_id`` on every line."""

    def __init__(
        self,
        directory: str | Path,
        trace_id: str,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.trace_id = trace_id
        self.path = self.directory / f"{trace_id}.jsonl"
        self._now = now or (lambda: datetime.now(UTC).isoformat())
        self._handle = self.path.open("a", encoding="utf-8")
        self._closed = False
        self._count = 0

    @property
    def event_count(self) -> int:
        return self._count

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> TraceEvent:
        if self._closed:
            raise TraceError("trace is closed")
        record = TraceEvent(
            ts=self._now(),
            trace_id=self.trace_id,
            event=event,
            payload=payload or {},
        )
        line = json.dumps(record.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        self._handle.write(line + "\n")
        self._handle.flush()
        self._count += 1
        return record

    # -- typed helpers: the only sanctioned way to write each event kind --------

    def run_started(
        self,
        *,
        task: str,
        config: Configuration,
        provider_name: str,
        model: str,
        tools: Sequence[ToolDescriptor] = (),
    ) -> None:
        """Record the task, the *whole* configuration, and the tool set as it was.

        Replay rebuilds the run from this event, so a config edited on disk after
        the fact cannot silently change what a replay means. No secret is recorded:
        the config names its key's environment variable and never holds the key.

        The descriptors are recorded because they are part of every prompt — they are
        sent to the model on every call, so their text is inside every prompt hash.
        Rebuilding them from the catalogue would make replay depend on the catalogue
        being unchanged, and would silently diverge for any run whose tool set was
        assembled some other way.

        Dumped by alias, so the config block is a valid config file body — you can
        copy it out of a trace and load it.
        """
        self.emit(
            "run_started",
            {
                "task": task,
                "config_name": config.name,
                "config": config.model_dump(mode="json", by_alias=True),
                "provider": provider_name,
                "model": model,
                "tools": [descriptor.model_dump(mode="json") for descriptor in tools],
            },
        )

    def model_call(self, record: ModelCallRecord) -> None:
        self.emit("model_call", record.model_dump(mode="json"))

    def tool_call(self, record: ToolCallRecord) -> None:
        self.emit("tool_call", record.model_dump(mode="json"))

    def context(self, record: ContextRecord) -> None:
        self.emit("context", record.model_dump(mode="json"))

    def failure(self, event: FailureEvent) -> None:
        self.emit("failure", event.model_dump(mode="json"))

    def run_finished(self, output: RunOutput) -> None:
        self.emit("run_finished", output.model_dump(mode="json"))

    def close(self) -> None:
        if not self._closed:
            self._handle.close()
            self._closed = True

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_trace(path: str | Path) -> TraceRecord:
    """Parse a trace file.

    A trailing partial line is tolerated and dropped: a process killed mid-write
    leaves one, and refusing to read the whole trace because of it would make the
    record less useful exactly when it matters most. A malformed line anywhere
    else is an error, not a thing to skip.
    """
    path = Path(path)
    if not path.exists():
        raise TraceError(f"no such trace: {path}")

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in raw_lines if line.strip()]

    events: list[TraceEvent] = []
    trace_id: str | None = None
    for index, line in enumerate(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            is_last = index == len(lines) - 1
            if is_last:
                break
            raise TraceError(f"{path}: line {index + 1} is not JSON: {exc}") from exc
        event = TraceEvent.model_validate(payload)
        if trace_id is None:
            trace_id = event.trace_id
        elif event.trace_id != trace_id:
            raise TraceError(
                f"{path}: line {index + 1} carries trace_id {event.trace_id!r}, "
                f"expected {trace_id!r}"
            )
        events.append(event)

    if trace_id is None:
        raise TraceError(f"{path}: contains no events")
    return TraceRecord(trace_id=trace_id, events=events)


def list_traces(directory: str | Path) -> list[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.jsonl"))


def model_call_records(trace: TraceRecord) -> list[ModelCallRecord]:
    """The recorded responses, in order — everything replay needs from the model."""
    return [ModelCallRecord.model_validate(e.payload) for e in trace.of("model_call")]


def tool_call_records(trace: TraceRecord) -> list[ToolCallRecord]:
    """The recorded tool outcomes, in order — everything replay needs from tools."""
    return [ToolCallRecord.model_validate(e.payload) for e in trace.of("tool_call")]


def recorded_config(trace: TraceRecord) -> Configuration:
    """The configuration as it was at run time, not as it is on disk now."""
    started = trace.first("run_started")
    if started is None:
        raise TraceError(f"{trace.trace_id}: no run_started event")
    raw = started.payload.get("config")
    if not isinstance(raw, dict):
        raise TraceError(f"{trace.trace_id}: run_started carries no configuration")
    return Configuration.model_validate(raw)


def recorded_task(trace: TraceRecord) -> str:
    started = trace.first("run_started")
    if started is None:
        raise TraceError(f"{trace.trace_id}: no run_started event")
    task = started.payload.get("task")
    if not isinstance(task, str):
        raise TraceError(f"{trace.trace_id}: run_started carries no task")
    return task


def recorded_descriptors(trace: TraceRecord) -> list[ToolDescriptor]:
    """The tool set as the model saw it. Part of every prompt, so replay needs it."""
    started = trace.first("run_started")
    if started is None:
        raise TraceError(f"{trace.trace_id}: no run_started event")
    raw = started.payload.get("tools")
    if not isinstance(raw, list):
        raise TraceError(
            f"{trace.trace_id}: run_started records no tool descriptors, so this trace "
            f"cannot be replayed — the prompts it recorded cannot be rebuilt"
        )
    return [ToolDescriptor.model_validate(item) for item in raw]


__all__ = [
    "TraceError",
    "TraceWriter",
    "list_traces",
    "model_call_records",
    "new_trace_id",
    "read_trace",
    "recorded_config",
    "recorded_descriptors",
    "recorded_task",
    "tool_call_records",
]
