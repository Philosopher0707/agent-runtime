"""The trace schema doc describes the trace schema.

A prose list of events drifts — that is this project's most-repeated lesson, from the count in
the roadmap to the `.env.example` that advertised six variables nothing read. A document that
describes a contract has to be checked against the contract, or it becomes an *accurate-looking*
description of something else.

Two directions, and both matter:

* an event the writer can emit and the doc does not list — undocumented behaviour
* an event the doc lists and the writer cannot emit — a doc describing a format that does not
  exist, which is worse than no doc because someone will code against it
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from runtime.schemas import TraceEvent

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "trace-schema.md"
WRITER = REPO_ROOT / "runtime" / "trace.py"

#: `self.emit("name"` or `self.emit(\n    "name"` — both appear in the writer.
EMIT = re.compile(r'self\.emit\(\s*"([a-z_]+)"')
#: A row of the doc's event table: `| `name` | ... |`
ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)


def emitted_events() -> set[str]:
    """Every event name `TraceWriter` can write, read from its source."""
    return set(EMIT.findall(WRITER.read_text(encoding="utf-8")))


def documented_events() -> set[str]:
    """Every event name in the doc's event table."""
    text = DOC.read_text(encoding="utf-8")
    assert "## The events" in text, "the doc no longer has an events section"
    section = text.split("## The events", 1)[1].split("\n## ", 1)[0]
    return set(ROW.findall(section))


def documented_envelope_fields() -> set[str]:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## The envelope", 1)[1].split("\n## ", 1)[0]
    return set(ROW.findall(section))


# -------------------------------------------------------------- non-vacuity


def test_the_doc_exists() -> None:
    assert DOC.is_file()


def test_both_sides_of_the_comparison_are_found() -> None:
    """A guard on the guard: if either parse stops working, equality proves nothing."""
    assert len(emitted_events()) >= 5, "the emit pattern has drifted"
    assert len(documented_events()) >= 5, "the doc's table format has drifted"


# ----------------------------------------------------------------- agreement


def test_the_doc_lists_every_event_the_writer_can_emit() -> None:
    undocumented = emitted_events() - documented_events()
    assert not undocumented, (
        f"{sorted(undocumented)} can be emitted but are not in docs/trace-schema.md. An event "
        f"nobody documented is an event nobody can replay against."
    )


def test_the_doc_invents_no_events() -> None:
    """The worse direction: a doc describing a format that does not exist."""
    invented = documented_events() - emitted_events()
    assert not invented, (
        f"docs/trace-schema.md documents {sorted(invented)}, which the writer cannot emit"
    )


def test_the_documented_envelope_matches_the_model() -> None:
    """The envelope is the one part of the format a reader will hard-code against."""
    assert documented_envelope_fields() == set(TraceEvent.model_fields), (
        "the documented envelope no longer matches TraceEvent"
    )


def test_every_documented_event_says_when_it_is_emitted() -> None:
    """A row with no `emitted` column would list an event without saying when it appears."""
    section = DOC.read_text(encoding="utf-8").split("## The events", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if ROW.match(line)]
    assert rows
    for row in rows:
        assert row.count("|") >= 4, f"row has no `emitted` column: {row}"
        assert row.rstrip().endswith("|"), f"row is truncated: {row}"


@pytest.mark.parametrize("event", sorted(emitted_events()))
def test_each_event_is_named_in_the_doc_body(event: str) -> None:
    """Listed in the table *and* described where the subtlety is."""
    assert f"`{event}`" in DOC.read_text(encoding="utf-8")
