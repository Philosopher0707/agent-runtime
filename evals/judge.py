"""The judge model, for the assertions deterministic checks cannot reach.

No case in the golden set uses this today. It exists so that adding a judged case is a
case-file change rather than a harness change — and so that "which scorer graded this
run" always has an answer.

A judged case is only worth adding where the thing being graded cannot be asserted:
tone, faithfulness to a long source, whether a summary omitted something that mattered.
If an assertion can reach it, use the assertion.
"""

from __future__ import annotations

from typing import Any

from runtime.schemas import RunOutput

DETERMINISTIC_SCORER = "deterministic"
JUDGE_SCORER = "judge"


class JudgeUnavailable(Exception):
    """No judge provider is configured, so a judged case cannot be graded."""


def grade(
    case: dict[str, Any],
    output: RunOutput,
    *,
    provider: object | None = None,
) -> str:
    """Grade a judged case. Raises until a judge provider is wired in.

    Deliberately unimplemented rather than silently lenient: a judge that returns
    "pass" when it cannot judge is worse than no judge at all.
    """
    raise JudgeUnavailable(
        f"case {case.get('id')!r} asks for judge scoring, but no judge provider is configured. "
        f"Either express the expectation as a deterministic assertion in the case file, or "
        f"wire a judge provider into evals.runner."
    )


__all__ = ["DETERMINISTIC_SCORER", "JUDGE_SCORER", "JudgeUnavailable", "grade"]
