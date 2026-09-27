"""Shared fixtures. Deliberately thin: tests build what they need explicitly."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from runtime.trace import TraceWriter, new_trace_id


@pytest.fixture
def tracer(tmp_path: Path) -> Iterator[TraceWriter]:
    """A trace writer in a temporary directory, closed afterwards."""
    with TraceWriter(tmp_path / "traces", new_trace_id()) as writer:
        yield writer


@pytest.fixture
def notes_root(tmp_path: Path) -> Path:
    root = tmp_path / "notes"
    root.mkdir(parents=True, exist_ok=True)
    return root
