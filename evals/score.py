"""Score the golden set. Prints the score; exits non-zero below the threshold.

Deterministic assertions first, and today only: every case in the set is graded by
assertions, and this script says so in its output. ``EVAL_THRESHOLD`` overrides the
required fraction (default 1.0 — the cases are deterministic, so anything less than
perfect means something regressed).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from evals.judge import JUDGE_SCORER
from evals.runner import CaseResult, run_all

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "evals" / "cases"
CONFIG_ROOT = REPO_ROOT / "configs"
WORK_DIR = REPO_ROOT / ".traces" / "evals"


def main() -> int:
    threshold = float(os.environ.get("EVAL_THRESHOLD", "1.0"))
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    results = run_all(cases_dir=CASES_DIR, config_root=CONFIG_ROOT, work_dir=WORK_DIR)
    _report(results, threshold)
    return 0 if _score(results) >= threshold else 1


def _score(results: list[CaseResult]) -> float:
    if not results:
        # An eval set that grades nothing cannot gate anything. Say so, and fail.
        return 0.0
    return sum(1 for result in results if result.passed) / len(results)


def _report(results: list[CaseResult], threshold: float) -> None:
    if not results:
        print("no eval cases found — an empty set cannot gate CI", file=sys.stderr)
        return

    width = max(len(result.case_id) for result in results)
    for result in results:
        mark = "pass" if result.passed else "FAIL"
        suffix = "" if result.passed else f"  reason={result.reason}"
        print(f"{mark}  {result.case_id:<{width}}  {result.status:<9}{suffix}")
        for problem in result.problems:
            print(f"        - {problem}")

    passed = sum(1 for result in results if result.passed)
    total = len(results)
    scorers = sorted({result.scorer for result in results})
    judge_used = JUDGE_SCORER in scorers

    print()
    print(f"score: {passed}/{total} = {passed / total:.3f}   threshold: {threshold:.3f}")
    print(f"scorer: {', '.join(scorers)}")
    print(f"traces: {WORK_DIR}")
    if judge_used:  # pragma: no cover - no judged case exists yet
        print("note: at least one case was graded by a judge model")

    failing = [result for result in results if not result.passed]
    if failing:
        print()
        print("failures:")
        for result in failing:
            print(f"  {result.case_id}: {result.trace_path}")


if __name__ == "__main__":
    raise SystemExit(main())
