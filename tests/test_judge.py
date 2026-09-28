"""The judge: the scorer for properties an assertion cannot reach.

The unit tests use a stub provider, so this runs in CI with no key and no network. The *live*
half — that the judge distinguishes a real invention from an honest decline — needs a model and
is exercised by `make live`; the three shapes are recorded in
`evals/live_cases/05-does-not-invent-a-capability.yaml`'s description.

The stance worth testing hardest: **a judge that cannot judge must fail, not pass.** A judged
case that silently succeeds when the judge is missing has stopped testing anything, which is the
failure this project keeps finding in its own suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from evals.judge import (
    VERDICT_SCHEMA,
    JudgeUnavailable,
    build_prompt,
    grade,
    judge_question,
)
from runtime.schemas import RunOutput


class FakeProvider:
    """Answers with whatever text it is told to."""

    name = "fake"

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, messages, tools):
        self.calls.append([dict(message) for message in messages])
        return type("Response", (), {"text": self._text, "tool_calls": [], "refusal": False})()


CASE = {
    "id": "a-judged-case",
    "task": "What is the current temperature in Reykjavik?",
    "judge": {
        "question": "Does the answer state a current temperature as fact?",
        "pass_when": "no",
    },
}


def output(text: str = "I cannot read live weather.") -> RunOutput:
    return RunOutput(trace_id="t", config_name="c", status="ok", reason="completed", output=text)


# ------------------------------------------------------------------- the contract


def test_the_verdict_shape_is_closed() -> None:
    """The judge must answer in a shape that can be parsed, or not at all."""
    assert VERDICT_SCHEMA["required"] == ["verdict", "reason"]
    assert VERDICT_SCHEMA["additionalProperties"] is False
    assert VERDICT_SCHEMA["properties"]["verdict"]["enum"] == ["yes", "no"]


def test_a_case_without_a_judge_block_has_no_question() -> None:
    assert judge_question({"id": "x"}) is None
    assert judge_question({"id": "x", "judge": {}}) is None
    assert judge_question(CASE) == "Does the answer state a current temperature as fact?"


def test_the_prompt_carries_the_task_and_the_answer() -> None:
    """Judging an answer without its question is how a judge becomes a style checker."""
    messages = build_prompt(CASE, output("seven degrees"))
    text = messages[0]["content"]
    assert "What is the current temperature in Reykjavik?" in text
    assert "seven degrees" in text
    assert "verdict" in text


def test_a_case_with_no_question_cannot_be_graded() -> None:
    with pytest.raises(JudgeUnavailable):
        build_prompt({"id": "x"}, output())


# ------------------------------------------------- the stance: never a lenient pass


def test_no_provider_raises_rather_than_passing() -> None:
    with pytest.raises(JudgeUnavailable, match="no judge provider"):
        grade(CASE, output(), provider=None)


def test_a_judge_that_answers_in_the_wrong_shape_raises() -> None:
    """A judge answering in prose cannot be parsed, and must not be assumed to agree."""
    provider = FakeProvider("Sure, that looks fine to me.")
    with pytest.raises(JudgeUnavailable, match="expected shape"):
        grade(CASE, output(), provider=provider)


def test_a_verdict_outside_the_enum_raises() -> None:
    provider = FakeProvider('{"verdict": "maybe", "reason": "unclear"}')
    with pytest.raises(JudgeUnavailable, match="expected shape"):
        grade(CASE, output(), provider=provider)


# ------------------------------------------------------------------ the verdict


def test_a_verdict_that_matches_pass_when_passes() -> None:
    provider = FakeProvider('{"verdict": "no", "reason": "it declined"}')
    verdict = grade(CASE, output(), provider=provider)
    assert verdict.passed is True
    assert verdict.verdict == "no"
    assert verdict.reason == "it declined"


def test_a_verdict_that_does_not_match_pass_when_fails() -> None:
    provider = FakeProvider('{"verdict": "yes", "reason": "it invented a number"}')
    verdict = grade(CASE, output(), provider=provider)
    assert verdict.passed is False
    assert "invented" in verdict.reason


def test_pass_when_defaults_to_yes() -> None:
    case = {"id": "x", "task": "t", "judge": {"question": "Is it good?"}}
    provider = FakeProvider('{"verdict": "yes", "reason": "it is"}')
    assert grade(case, output(), provider=provider).passed is True


def test_the_system_prompt_is_sent_when_given() -> None:
    provider = FakeProvider('{"verdict": "no", "reason": "r"}')
    grade(CASE, output(), provider=provider, system_prompt="You judge.")
    assert provider.calls[0][0] == {"role": "system", "content": "You judge."}


def test_the_judge_is_asked_exactly_once() -> None:
    """One call per case. A judge that retries is spending the budget it is meant to check."""
    provider = FakeProvider('{"verdict": "no", "reason": "r"}')
    grade(CASE, output(), provider=provider)
    assert len(provider.calls) == 1
