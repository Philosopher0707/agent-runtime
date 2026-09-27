"""Measure the injection-marker rule against the corpus. Prints a report.

Run it after touching the marker list in ``context.sanitize``. The numbers it prints are
the evidence behind ``docs/decisions/0011``, and the thresholds it exits on are the same
ones ``tests/test_marker_precision.py`` enforces — so the report and the tripwire cannot
disagree.

    make markers          # report, exits non-zero below threshold
    make markers ARGS=--repo   # also sweep this repository's own prose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from context.sanitize import assess
from evals.markers import Assessment, measure, measure_repo, render

REPO_ROOT = Path(__file__).resolve().parent.parent

#: A false positive refuses a legitimate run, and every sample in the benign corpus was
#: chosen by hand to represent output a real tool returns. There is no rate of refusing
#: those that is acceptable, so the budget is zero.
#:
#: The risk this creates is obvious and worth naming: the cheapest way to make a failing
#: suite pass is to delete the sample that fails. Deleting a benign sample is the wrong
#: fix; fixing the marker or the sample's realism is the right one.
MAX_FALSE_POSITIVE_RATE = 0.0

#: Unambiguous payloads are the ones the guardrail exists for. Missing any is a defect.
MIN_RECALL = 1.0


def rule(text: str) -> Assessment:
    """Adapt the real guardrail to the corpus's assessment protocol."""
    result = assess(text)
    return Assessment(trip=result.trip, markers=result.markers, reason=result.reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the injection-marker rule.")
    parser.add_argument("--repo", action="store_true", help="also sweep this repository")
    parser.add_argument("--quiet", action="store_true", help="thresholds only")
    args = parser.parse_args(argv)

    measurement = measure(rule)
    repo = measure_repo(rule, REPO_ROOT) if args.repo else None

    if not args.quiet:
        print(render(measurement, repo))
        print()

    fp_rate = measurement.false_positive_rate
    recall = measurement.recall
    failures: list[str] = []

    if fp_rate > MAX_FALSE_POSITIVE_RATE:
        failures.append(
            f"false-positive rate {fp_rate:.1%} exceeds the {MAX_FALSE_POSITIVE_RATE:.0%} budget"
        )
    if recall < MIN_RECALL:
        missed = measurement.hostile_unambiguous.total - measurement.hostile_unambiguous.tripped
        failures.append(f"recall {recall:.1%}: {missed} unambiguous payload(s) missed")

    print(
        f"thresholds: false positives <= {MAX_FALSE_POSITIVE_RATE:.0%}, recall >= {MIN_RECALL:.0%}"
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if not failures:
        print("ok")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
