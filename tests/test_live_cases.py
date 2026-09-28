"""The live suite's own contract.

The live cases are the only place a real model's behaviour is asserted, and the vocabulary they
assert with had never been checked against the code that enforces it. Two things were wrong:

* **An unrecognised assertion kind was silently skipped.** A typo in a case — `output_matchs` for
  `output_matches` — made the case pass while testing nothing. Found because a case used
  `output_matches_all` before it existed and ran green.
* **The set of known kinds was hand-written from a partial read** and missed two kinds
  (`steps_at_most`, `model_calls_at_most`) that are enforced further down the function. The set is
  derived from the checker now, and this file asserts they agree.

A case that tests less than it says is the failure this project keeps finding in its own suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from evals.live import ASSERTION_KINDS, check_properties
from runtime.schemas import RunOutput

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_PY = REPO_ROOT / "evals" / "live.py"
LIVE_CASES = REPO_ROOT / "evals" / "live_cases"

#: `expect["kind"]` or `expect.get("kind"` — how the checker reaches into a case.
REACHES_IN = re.compile(r'expect(?:\.get\(|\[)"([a-z_]+)"')


def kinds_the_checker_enforces() -> set[str]:
    """Read from the source, not remembered.

    Every `expect[...]` the checker touches, minus the line that defines the set itself.
    """
    found: set[str] = set()
    for line in LIVE_PY.read_text(encoding="utf-8").splitlines():
        if "ASSERTION_KINDS" in line:
            continue
        found |= set(REACHES_IN.findall(line))
    return found


def test_the_set_matches_what_the_checker_enforces() -> None:
    """The set is derived, so adding an assertion kind cannot leave the set behind.

    This is the check that would have caught the hand-written set missing two kinds — which
    would have made the refusal below fail three valid cases.
    """
    enforced = kinds_the_checker_enforces()
    assert enforced, "the pattern has drifted and now finds nothing"
    assert enforced == set(ASSERTION_KINDS), (
        f"checker enforces {sorted(enforced)}, set says {sorted(ASSERTION_KINDS)}"
    )


def test_every_live_case_uses_only_known_kinds() -> None:
    """A case is data, so this is a check over the files rather than over one run."""
    offenders: list[str] = []
    for path in sorted(LIVE_CASES.glob("*.yaml")):
        case = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        unknown = sorted(set(case.get("expect") or {}) - ASSERTION_KINDS)
        if unknown:
            offenders.append(f"{path.name}: {unknown}")
    assert not offenders, (
        f"cases assert with kinds the checker does not know: {offenders}. "
        f"An unknown kind is a typo that makes a case test less than it says."
    )


def test_the_case_check_is_not_vacuous() -> None:
    """It must be reading real cases with real expectations."""
    cases = [yaml.safe_load(p.read_text(encoding="utf-8")) or {} for p in LIVE_CASES.glob("*.yaml")]
    assert len(cases) >= 5
    assert all(case.get("expect") for case in cases), "a case with no expectations proves nothing"


def test_an_unknown_kind_is_refused_not_ignored() -> None:
    """The bug: an unrecognised key was skipped, so the case passed while testing nothing."""
    output = RunOutput(trace_id="t", config_name="c", status="ok", reason="completed")
    problems = check_properties({"output_matchs": "anything"}, output)
    assert problems, "an unknown assertion kind was silently accepted"
    assert "unknown assertion kind" in problems[0]


def test_output_matches_all_requires_every_pattern() -> None:
    """The kind that was used before it existed."""
    output = RunOutput(
        trace_id="t", config_name="c", status="ok", reason="completed", output='{"a": 1, "b": 2}'
    )
    assert check_properties({"output_matches_all": ['"a"', '"b"']}, output) == []
    assert check_properties({"output_matches_all": ['"a"', '"zzz"']}, output)


def test_a_known_kind_still_passes_when_it_should() -> None:
    """A guard on the refusal: it must not fire on valid expectations."""
    output = RunOutput(trace_id="t", config_name="c", status="ok", reason="completed", steps=2)
    assert check_properties({"status_in": ["ok"], "steps_at_most": 3}, output) == []


@pytest.mark.parametrize("kind", sorted(ASSERTION_KINDS))
def test_every_kind_in_the_set_is_reachable(kind: str) -> None:
    """Every name in the set is one the checker actually reads.

    Catches the reverse of the drift: a kind left in the set after its enforcement is removed,
    which would let a case assert with a key nothing honours.
    """
    assert kind in kinds_the_checker_enforces(), f"{kind!r} is in the set but nothing enforces it"
