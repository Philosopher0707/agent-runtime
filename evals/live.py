"""The live suite: properties, not values.

The golden set asserts *exact* outcomes — `output_exact`, `model_calls: 2`, `attempts: 1` —
because the stub is deterministic. A real model is not, so a live run cannot assert those
things without asserting the model's wording.

So this suite asserts what must hold **regardless of wording**: the status, which tools were
called, that the guardrail held, that a required fact appears. A real model may phrase
`21 * 2 = 42` a hundred ways; all of them are correct and only some are `"42"`.

Two things this deliberately is not:

* **Not in `make ci`.** It needs an API key and it is non-deterministic, so it cannot gate a
  push. The CI-safe half is `tests/test_recorded_runs.py`, which replays recorded real traces.
* **Not a replacement for the golden set.** The stub suite is the *contract* test — given
  these model outputs, the loop does exactly this. This suite is a *model* test: given a real
  model, does the system behave.

    make live              # needs AGENT_API_KEY; prints a score, exits non-zero on a violation
    make live ARGS=--case single-tool-call

Every run writes a trace. Promoting one into `evals/fixtures/` is how a live behaviour becomes
a CI check without a key.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evals.judge import (
    DETERMINISTIC_SCORER,
    JUDGE_SCORER,
    JudgeUnavailable,
    grade,
    judge_question,
)
from runtime.config import (
    ConfigError,
    apply_overrides,
    load_config_by_name,
    load_env_file,
    read_api_key,
)
from runtime.factory import build_provider, build_tools
from runtime.loop import run
from runtime.schemas import RunOutput
from runtime.trace import TraceWriter, new_trace_id

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_CASES_DIR = REPO_ROOT / "evals" / "live_cases"
LIVE_TRACE_DIR = REPO_ROOT / ".traces" / "live"
CONFIG_ROOT = REPO_ROOT / "configs"
NOTES_ROOT = REPO_ROOT / ".notes" / "live"

#: The configuration the judge's model and instruction come from. A separate configuration on
#: purpose: the judge should be able to be a *different* model from the one under test, and a
#: self-graded case is a weaker thing that ought to be visible rather than accidental.
JUDGE_CONFIG = "judge"


@dataclass
class LiveResult:
    case_id: str
    passed: bool
    problems: list[str] = field(default_factory=list)
    status: str = ""
    trace_path: Path | None = None
    #: Which scorer graded this case. "Which scorer graded it" should always have an answer —
    #: a judged property and an asserted one are different kinds of claim.
    scorer: str = DETERMINISTIC_SCORER


def load_cases(directory: Path = LIVE_CASES_DIR) -> list[tuple[Path, dict[str, Any]]]:
    paths = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    cases: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "id" not in raw:
            raise ValueError(f"{path}: a live case must be a mapping with an id")
        cases.append((path, raw))
    return cases


#: Every assertion kind this vocabulary understands.
#:
#: Closed on purpose. A key outside this set is a typo, and an ignored typo makes a case pass
#: while testing less than it claims. Written after `output_matches_all` was used in a case
#: before it existed — and the case ran green.
ASSERTION_KINDS = frozenset(
    {
        "status_in",
        "status_not_in",
        "tools_called_includes",
        "tools_called_excludes",
        "failure_classes_exclude",
        "output_matches",
        "output_matches_all",
        "output_min_chars",
        "notes_written",
        "steps_at_most",
        "model_calls_at_most",
    }
)


def check_properties(
    expect: dict[str, Any], output: RunOutput, *, notes_root: Path | None = None
) -> list[str]:
    """Every property, checked. Returns one message per violation.

    The vocabulary is deliberately small and named after the question it answers. Anything
    that needs an exact value belongs in the golden set, not here.
    """
    problems: list[str] = []
    called = [record.name for record in output.tool_calls]
    classes = {str(item) for item in output.failure_classes}

    #: Refused rather than skipped. An unrecognised key is a typo, and a silently ignored
    #: assertion makes a case pass while testing less than it claims — which is the failure
    #: this project keeps finding in its own suite. Written after `output_matches_all` was
    #: used in a case before it existed, and the case ran green.
    unknown = sorted(set(expect) - ASSERTION_KINDS)
    if unknown:
        problems.append(
            f"unknown assertion kind(s) {unknown}. Known: {sorted(ASSERTION_KINDS)}. "
            f"A case must not test less than it says."
        )

    if "status_in" in expect and str(output.status) not in expect["status_in"]:
        problems.append(f"status_in: {output.status} not in {expect['status_in']}")
    if "status_not_in" in expect and str(output.status) in expect["status_not_in"]:
        problems.append(f"status_not_in: {output.status} is in {expect['status_not_in']}")

    for name in expect.get("tools_called_includes", []):
        if name not in called:
            problems.append(f"tools_called_includes: {name!r} was not called (called: {called})")
    for name in expect.get("tools_called_excludes", []):
        if name in called:
            problems.append(f"tools_called_excludes: {name!r} was called")

    for name in expect.get("failure_classes_exclude", []):
        if name in classes:
            problems.append(f"failure_classes_exclude: {name!r} is present in {sorted(classes)}")

    if "output_matches" in expect:
        text = output.output or ""
        if not re.search(expect["output_matches"], text, re.IGNORECASE):
            problems.append(
                f"output_matches: /{expect['output_matches']}/ not found in {text[:120]!r}"
            )
    #: All of these must match. `output_matches` takes one pattern, and a case that needs two
    #: independent facts about the output should not have to fuse them into one regex.
    for pattern in expect.get("output_matches_all", []):
        text = output.output or ""
        if not re.search(pattern, text, re.IGNORECASE):
            problems.append(f"output_matches_all: /{pattern}/ not found in {text[:120]!r}")
    if "output_min_chars" in expect and len(output.output or "") < expect["output_min_chars"]:
        problems.append(
            f"output_min_chars: got {len(output.output or '')}, "
            f"expected at least {expect['output_min_chars']}"
        )

    if "steps_at_most" in expect and output.steps > expect["steps_at_most"]:
        problems.append(f"steps_at_most: {output.steps} > {expect['steps_at_most']}")
    if "model_calls_at_most" in expect and output.model_calls > expect["model_calls_at_most"]:
        problems.append(
            f"model_calls_at_most: {output.model_calls} > {expect['model_calls_at_most']}"
        )

    if "notes_written" in expect and notes_root is not None:
        wrote = any(path.is_file() for path in notes_root.rglob("*"))
        if wrote != expect["notes_written"]:
            problems.append(f"notes_written: expected {expect['notes_written']}, got {wrote}")

    return problems


def _scripted_tools(spec: dict[str, Any]) -> list[Any]:
    from tools.scripted import ScriptedTool

    return [
        ScriptedTool(
            name=name,
            behaviour=options.get("behaviour", "ok"),
            result=options.get("result", f"{name} result"),
            error=options.get("error", f"{name} failed"),
            side_effect=bool(options.get("side_effect", False)),
            idempotent=bool(options.get("idempotent", True)),
            optional=bool(options.get("optional", True)),
        )
        for name, options in spec.items()
    ]


def _judge_problems(case: dict[str, Any], output: RunOutput) -> list[str]:
    """Grade the case with a judge model, or say plainly why it could not be graded.

    A judged case that passes when the judge is missing has stopped testing anything, so every
    failure to judge is a *problem* rather than a skip. That is the placeholder's own stance
    and it is the right one: a silent pass is the failure this project keeps finding.
    """
    try:
        config = load_config_by_name(JUDGE_CONFIG, root=CONFIG_ROOT)
    except ConfigError as exc:
        return [f"judge: no judge configuration ({exc})"]
    if not read_api_key(config.provider):
        return [f"judge: {config.provider.api_key_env} is not set, so the judge cannot run"]

    provider = build_provider(config.provider)
    try:
        verdict = grade(case, output, provider=provider, system_prompt=config.system_prompt)
    except JudgeUnavailable as exc:
        return [f"judge: {exc}"]

    print(f"    judge: {verdict.verdict} — {verdict.reason}")
    if verdict.passed:
        return []
    return [f"judge: the answer failed the judged property — {verdict.reason}"]


def run_case(case: dict[str, Any]) -> LiveResult:
    case_id = str(case["id"])
    notes_root = NOTES_ROOT / case_id
    notes_root.mkdir(parents=True, exist_ok=True)

    config = apply_overrides(
        load_config_by_name(case.get("config", "openai_compat"), root=CONFIG_ROOT),
        case.get("overrides") or {},
    )
    key = read_api_key(config.provider)
    if not key:
        raise ConfigError(
            f"{case_id}: no API key. This suite calls a real model — put "
            f"{config.provider.api_key_env} in .env, or run `make ci` for the deterministic "
            f"suite and `pytest tests/test_recorded_runs.py` for the recorded ones."
        )

    extra = _scripted_tools(case.get("tools") or {})
    registry = build_tools(
        config,
        extra=extra,
        overrides={"write_note": {"root": notes_root}},
        sleep=lambda _seconds: None,
        jitter=lambda _low, _high: 0.0,
    )
    provider = build_provider(config.provider)
    trace_id = new_trace_id()
    try:
        with TraceWriter(LIVE_TRACE_DIR, trace_id) as tracer:
            output = run(
                str(case.get("task", "")),
                config=config,
                provider=provider,
                tools=registry,
                tracer=tracer,
                confirmation_token=case.get("confirmation_token"),
            )
    finally:
        registry.close()

    problems = check_properties(case.get("expect") or {}, output, notes_root=notes_root)
    scorer = DETERMINISTIC_SCORER
    if judge_question(case):
        scorer = JUDGE_SCORER
        problems.extend(_judge_problems(case, output))

    return LiveResult(
        case_id=case_id,
        passed=not problems,
        problems=problems,
        status=str(output.status),
        scorer=scorer,
        trace_path=LIVE_TRACE_DIR / f"{trace_id}.jsonl",
    )


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description="Run the live suite against a real model.")
    parser.add_argument("--case", action="append", default=None, help="run only these ids")
    parser.add_argument("--dir", default=str(LIVE_CASES_DIR))
    args = parser.parse_args(argv)

    cases = load_cases(Path(args.dir))
    if args.case:
        wanted = set(args.case)
        cases = [(path, case) for path, case in cases if case["id"] in wanted]
    if not cases:
        print("no live cases selected", file=sys.stderr)
        return 1

    print(f"live suite: {len(cases)} case(s) against a real model\n")
    results: list[LiveResult] = []
    for _path, case in cases:
        try:
            result = run_case(case)
        except ConfigError as exc:
            print(f"cannot run: {exc}", file=sys.stderr)
            return 1
        results.append(result)
        mark = "pass" if result.passed else "FAIL"
        print(f"{mark}  {result.case_id:<34} {result.status}")
        for problem in result.problems:
            print(f"        - {problem}")

    passed = sum(1 for result in results if result.passed)
    print(f"\nscore: {passed}/{len(results)}")
    print(f"traces: {LIVE_TRACE_DIR}   <- promote a good one into evals/fixtures/ for CI")

    failing = [result for result in results if not result.passed]
    if failing:
        print("\nfailures:")
        for result in failing:
            print(f"  {result.case_id}: {result.trace_path}")
    return 0 if not failing else 1


if __name__ == "__main__":
    raise SystemExit(main())
