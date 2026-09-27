"""The golden-set runner.

Each case is a YAML file: a configuration, a scripted model, optionally some scripted
tools, and what the outcome must be. Nothing here is mocked at the Python level — a
case drives the real loop, the real budget, the real tracer, and the real dispatch
policy. The only substitutions are the model and the tools, and both are substituted
through the same protocols the real ones satisfy.

This is why the harness was built before the agent: against a stub, it forced the
contract to be real.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evals.judge import DETERMINISTIC_SCORER
from providers.base import Provider
from providers.stub import StubProvider
from runtime.config import Configuration, apply_overrides, load_config_by_name
from runtime.factory import build_provider
from runtime.loop import run
from runtime.schemas import RunOutput, ToolCallRecord, TraceRecord
from runtime.trace import TraceWriter, new_trace_id, read_trace
from tools.catalogue import build_registry
from tools.registry import Tool
from tools.scripted import ScriptedTool


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    problems: list[str] = field(default_factory=list)
    status: str = ""
    reason: str | None = None
    trace_path: Path | None = None
    scorer: str = DETERMINISTIC_SCORER


def load_case(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: a case must be a mapping")
    if "id" not in raw:
        raise ValueError(f"{path}: a case must declare an id")
    return raw


def load_cases(cases_dir: str | Path) -> list[tuple[Path, dict[str, Any]]]:
    cases_dir = Path(cases_dir)
    paths = sorted(cases_dir.glob("*.yaml")) + sorted(cases_dir.glob("*.yml"))
    return [(path, load_case(path)) for path in paths]


def run_case(
    case: dict[str, Any],
    *,
    config_root: str | Path,
    work_dir: str | Path,
) -> CaseResult:
    case_id = str(case["id"])
    base = Path(work_dir) / case_id
    notes_root = base / "notes"
    trace_dir = base / "traces"
    notes_root.mkdir(parents=True, exist_ok=True)

    config = apply_overrides(
        load_config_by_name(case.get("config", "default"), root=config_root),
        case.get("overrides") or {},
    )

    extra_tools = _scripted_tools(case.get("tools") or {})
    registry = build_registry(
        [*config.tools, *(tool.name for tool in extra_tools)],
        extra=extra_tools,
        tool_kwargs={"write_note": {"root": notes_root}},
        # Retries must not actually sleep in the harness, or the golden set is slow.
        sleep=lambda _seconds: None,
        jitter=lambda _low, _high: 0.0,
    )
    provider = _build_provider(case, config)
    trace_id = new_trace_id()

    try:
        with TraceWriter(trace_dir, trace_id) as tracer:
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

    trace_path = trace_dir / f"{trace_id}.jsonl"
    trace = read_trace(trace_path)
    problems = check(case.get("expect") or {}, output, trace, notes_root)
    return CaseResult(
        case_id=case_id,
        passed=not problems,
        problems=problems,
        status=str(output.status),
        reason=output.reason,
        trace_path=trace_path,
    )


def run_all(
    *,
    cases_dir: str | Path,
    config_root: str | Path,
    work_dir: str | Path,
) -> list[CaseResult]:
    return [
        run_case(case, config_root=config_root, work_dir=work_dir)
        for _path, case in load_cases(cases_dir)
    ]


# --------------------------------------------------------------------- internals


def _scripted_tools(spec: dict[str, Any]) -> list[Tool]:
    return [
        ScriptedTool(
            name=name,
            behaviour=options.get("behaviour", "ok"),
            result=options.get("result", f"{name} result"),
            error=options.get("error", f"{name} failed"),
            side_effect=bool(options.get("side_effect", False)),
            idempotent=bool(options.get("idempotent", True)),
            optional=bool(options.get("optional", True)),
            timeout_s=float(options.get("timeout_s", 1.0)),
        )
        for name, options in spec.items()
    ]


def _build_provider(case: dict[str, Any], config: Configuration) -> Provider:
    stub = case.get("stub")
    if stub is None:
        return build_provider(config.provider)
    return StubProvider(
        model=config.provider.model,
        script=stub.get("script") or [],
        default_final=stub.get("final", "Done."),
        price_input_per_mtok=config.provider.price_input_per_mtok,
        price_output_per_mtok=config.provider.price_output_per_mtok,
        fail_on_call=stub.get("fail_on_call"),
        fail_message=stub.get("fail_message", "simulated provider failure"),
    )


def check(
    expect: dict[str, Any],
    output: RunOutput,
    trace: TraceRecord,
    notes_root: Path,
) -> list[str]:
    """Every expectation, checked. Returns one message per violation."""
    problems: list[str] = []

    def want(key: str, actual: Any, expected: Any) -> None:
        if actual != expected:
            problems.append(f"{key}: expected {expected!r}, got {actual!r}")

    if "status" in expect:
        want("status", str(output.status), expect["status"])
    if "reason" in expect:
        want("reason", output.reason, expect["reason"])
    if "failure_classes" in expect:
        want(
            "failure_classes",
            sorted(str(c) for c in output.failure_classes),
            sorted(expect["failure_classes"]),
        )
    if "model_calls" in expect:
        want("model_calls", output.model_calls, expect["model_calls"])
    if "steps" in expect:
        want("steps", output.steps, expect["steps"])
    if "output_exact" in expect:
        want("output_exact", output.output, expect["output_exact"])
    if "output_contains" in expect and expect["output_contains"] not in (output.output or ""):
        problems.append(f"output_contains: {expect['output_contains']!r} not in the answer")
    if "clarifying_question" in expect:
        want(
            "clarifying_question",
            output.clarifying_question is not None,
            expect["clarifying_question"],
        )
    if "clarifying_question_contains" in expect and expect["clarifying_question_contains"] not in (
        output.clarifying_question or ""
    ):
        problems.append("clarifying_question_contains: not found")
    if "clarifications_suppressed" in expect:
        want(
            "clarifications_suppressed",
            output.clarifications_suppressed,
            expect["clarifications_suppressed"],
        )
    if "guardrail" in expect:
        tripped = {str(e.guardrail) for e in output.failures if e.guardrail is not None}
        if expect["guardrail"] not in tripped:
            problems.append(
                f"guardrail: {expect['guardrail']!r} did not trip (tripped: {sorted(tripped)})"
            )

    problems.extend(_check_tool_records(expect, output))
    problems.extend(_check_context(expect, trace))
    problems.extend(_check_redaction(expect, trace))

    if "notes_written" in expect:
        wrote = any(path.is_file() for path in notes_root.rglob("*"))
        want("notes_written", wrote, expect["notes_written"])

    return problems


def _check_redaction(expect: dict[str, Any], trace: TraceRecord) -> list[str]:
    """Redaction is checked against the *file*, not against a summary event.

    ``trace_excludes`` serialises every recorded event and asserts a string is absent from
    all of them. A check that only looked at the summary would pass while the data sat in
    an event the summary does not describe.
    """
    problems: list[str] = []

    if "trace_redacted" in expect:
        present = trace.first("redaction") is not None
        if present != expect["trace_redacted"]:
            problems.append(f"trace_redacted: expected {expect['trace_redacted']}, got {present}")

    if "trace_excludes" in expect:
        blob = json.dumps([event.model_dump(mode="json") for event in trace.events])
        for secret in expect["trace_excludes"]:
            if secret in blob:
                problems.append(f"trace_excludes: {secret!r} is present in the trace")

    return problems


def _check_tool_records(expect: dict[str, Any], output: RunOutput) -> list[str]:
    problems: list[str] = []
    recorded = [record for record in output.tool_calls if record.name != "ask_clarification"]

    if "tools" in expect:
        expected_records = expect["tools"]
        if len(expected_records) != len(recorded):
            problems.append(
                f"tools: expected {len(expected_records)} tool call(s), got {len(recorded)} "
                f"({[record.name for record in recorded]})"
            )
        else:
            for index, (expected, actual) in enumerate(
                zip(expected_records, recorded, strict=True)
            ):
                problems.extend(_match_record(index, expected, actual))

    if "tools_not_called" in expect:
        called = {record.name for record in recorded}
        for name in expect["tools_not_called"]:
            if name in called:
                problems.append(f"tools_not_called: {name!r} was called")

    return problems


def _match_record(index: int, expected: dict[str, Any], actual: ToolCallRecord) -> list[str]:
    problems: list[str] = []
    for key, value in expected.items():
        if key == "name":
            matched = actual.name == value
        elif key == "outcome":
            matched = str(actual.outcome) == value
        elif key == "attempts":
            matched = actual.attempts == value
        elif key == "attempt_outcomes":
            matched = [str(o) for o in actual.attempt_outcomes] == value
        elif key == "confirmation_applied":
            matched = actual.confirmation_applied == value
        elif key == "error_contains":
            matched = value in (actual.error or "")
        elif key == "result_contains":
            matched = value in (actual.result or "")
        elif key == "arguments_contain":
            matched = all(actual.arguments.get(k) == v for k, v in value.items())
        else:
            problems.append(f"tools[{index}]: unknown expectation key {key!r}")
            continue
        if not matched:
            problems.append(f"tools[{index}].{key}: expected {value!r}, got {actual!r}")
    return problems


def _check_context(expect: dict[str, Any], trace: TraceRecord) -> list[str]:
    problems: list[str] = []
    context_events = [event.payload for event in trace.of("context")]
    if not context_events:
        if any(
            key in expect
            for key in ("summarised_results_min", "dropped_messages", "system_prompt_present")
        ):
            problems.append("context: the trace records no context events")
        return problems

    if "system_prompt_present" in expect:
        present = all(bool(event.get("system_prompt_present")) for event in context_events)
        if present != expect["system_prompt_present"]:
            problems.append(
                f"system_prompt_present: expected {expect['system_prompt_present']}, got {present}"
            )
    if "summarised_results_min" in expect:
        total = sum(int(event.get("summarised_results") or 0) for event in context_events)
        if total < expect["summarised_results_min"]:
            problems.append(
                f"summarised_results_min: expected at least "
                f"{expect['summarised_results_min']}, got {total}"
            )
    if "dropped_messages" in expect:
        total = sum(int(event.get("dropped_messages") or 0) for event in context_events)
        if total != expect["dropped_messages"]:
            problems.append(f"dropped_messages: expected {expect['dropped_messages']}, got {total}")
    return problems


__all__ = [
    "CaseResult",
    "check",
    "load_case",
    "load_cases",
    "run_all",
    "run_case",
]
