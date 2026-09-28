"""Starting a run from inside a run.

The first genuinely new mechanism the agent layer needs, and the four things
[decisions/0028](../../docs/decisions/0028-the-agent-is-a-capability.md) settles before it
existed: budget, trace, confirmation, failure.

These tests use stub configs written to a temporary root, so they exercise the real wiring —
`run_task`, the runner, the budget allocation, the trace — with no key and no spend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from runtime.factory import run_task
from runtime.schemas import RunRequest
from runtime.trace import read_trace
from tools.registry import ToolError
from tools.subagent import SpawnAgentArgs, SpawnAgentTool

MINIMAL: dict[str, Any] = {
    "system_prompt": "A stub.",
    "provider": {"kind": "stub", "model": "stub", "stub_final": "child done"},
    # Deliberately roomy. A child gets a *share* of this, and a share of a budget that only just
    # fits the parent is a child that cannot finish — which is the allocation working, not a bug.
    "budget": {
        "max_steps": 12,
        "max_tokens_total": 40_000,
        "max_wall_clock_s": 30,
        "max_cost_usd": 0.05,
    },
}


def write_config(root: Path, name: str, **overrides: Any) -> None:
    body = {**MINIMAL, **overrides, "name": name}
    (root / f"{name}.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


@pytest.fixture
def configs(tmp_path: Path) -> Path:
    root = tmp_path / "configs"
    root.mkdir()
    write_config(root, "child")
    return root


def parent_config(root: Path, *, share: float = 0.5, inherit: bool = False) -> None:
    """A parent whose scripted model calls `spawn_agent` once and then answers."""
    write_config(
        root,
        "parent",
        tools=["spawn_agent"],
        tool_options={
            "spawn_agent": {"config": "child", "share": share, "inherit_confirmation": inherit}
        },
        provider={
            "kind": "stub",
            "model": "stub",
            "stub_script": [
                {"tool_calls": [{"name": "spawn_agent", "arguments": {"task": "check this"}}]},
                {"text": '{"findings": []}'},
            ],
            "stub_final": '{"findings": []}',
        },
    )


def run_parent(root: Path, tmp_path: Path, *, token: str | None = "caller-token"):
    return run_task(
        RunRequest(task="do the thing", config="parent", confirmation_token=token),
        config_root=root,
        trace_dir=tmp_path / "traces",
    )


# --------------------------------------------------------------- the mechanism


def test_a_parent_can_start_a_run(configs: Path, tmp_path: Path) -> None:
    parent_config(configs)
    output = run_parent(configs, tmp_path)

    assert output.status == "ok"
    names = [record.name for record in output.tool_calls]
    assert names == ["spawn_agent"], "the spawn did not happen"


def test_the_child_writes_its_own_trace_and_the_parent_names_it(
    configs: Path, tmp_path: Path
) -> None:
    """The linkage is the parent's tool_call record — no new trace event, because the existing
    one already carries everything needed to find the child."""
    parent_config(configs)
    output = run_parent(configs, tmp_path)

    record = output.tool_calls[0]
    assert "[sub-agent]" in record.result
    child_id = record.result.split("trace=")[1].split()[0]

    traces = sorted((tmp_path / "traces").glob("*.jsonl"))
    assert len(traces) == 2, "one trace for the parent, one for the child"
    child_trace = tmp_path / "traces" / f"{child_id}.jsonl"
    assert child_trace.is_file(), "the parent named a trace that does not exist"

    # And the child's trace is a run, not a fragment of the parent's.
    child = read_trace(child_trace)
    assert child.first("run_started") is not None
    assert child.first("run_finished") is not None
    assert child.first("run_started").payload["task"] == "check this"


def test_the_parent_is_charged_what_the_child_spent(configs: Path, tmp_path: Path) -> None:
    """Charged the *usage*, not the allocation. Charging the allocation would make a spawn cost
    the parent its whole share whether or not the child used it."""
    parent_config(configs)
    output = run_parent(configs, tmp_path)

    assert output.tokens_total > 0
    # The stub is free, so cost is zero — but the child's tokens must be in the parent's total,
    # which they are only if `charge` ran.
    assert output.tool_calls[0].result.count("cost=$") == 1


def test_a_child_cannot_spawn(configs: Path, tmp_path: Path) -> None:
    """Depth one, and it is enforced by *absence*: the child's tools are built with no runner,
    so its `spawn_agent` — if its configuration names one — refuses."""
    tool = SpawnAgentTool(runner=None)
    with pytest.raises(ToolError, match="no runner was wired in"):
        tool.invoke(SpawnAgentArgs(task="x", confirmation_token="t"))


def test_the_depth_limit_is_also_stated(configs: Path, tmp_path: Path) -> None:
    """Belt and braces: the class says it too, so a runner wired into a child by mistake still
    stops at one level."""
    tool = SpawnAgentTool(runner=lambda request: None, max_depth=0)  # type: ignore[arg-type,return-value]
    with pytest.raises(ToolError, match="depth limit"):
        tool.invoke(SpawnAgentArgs(task="x", confirmation_token="t"))


# ------------------------------------------------------------- the confirmation


def child_that_acts(root: Path) -> None:
    """A child whose scripted model reaches for a side effect.

    The only way to observe token inheritance: the child must *try* to act. A child with no
    side-effecting tool cannot show the difference between having a token and not.
    """
    write_config(
        root,
        "child",
        tools=["write_note"],
        tool_options={"write_note": {"root": str(root / "notes")}},
        provider={
            "kind": "stub",
            "model": "stub",
            "stub_script": [
                {
                    "tool_calls": [
                        {"name": "write_note", "arguments": {"filename": "a.txt", "text": "x"}}
                    ]
                },
                {"text": "child done"},
            ],
            "stub_final": "child done",
        },
    )


def child_status(tmp_path: Path) -> str:
    """The status of the child's own run, read from its trace.

    Identified by the **trace id the parent named**, not by searching for the task string — the
    parent's own `run_started` records its whole configuration, including the scripted call that
    contains that string. A first version searched for it and was flaky: which trace sorted
    first decided whether the test passed.
    """
    traces = sorted((tmp_path / "traces").glob("*.jsonl"))
    for path in traces:
        started = read_trace(path).first("run_started")
        if started is not None and started.payload["task"] == "check this":
            finished = read_trace(path).first("run_finished")
            assert finished is not None
            return str(finished.payload["status"])
    raise AssertionError("no child trace was written")


def test_a_child_does_not_inherit_the_callers_token_by_default(
    configs: Path, tmp_path: Path
) -> None:
    """The gate exists so the *principal* decides, and a sub-agent's caller is the parent — which
    is not the principal, and did not see the task decomposed.

    Observable only because the child *tries to act*: with no inherited token its `write_note` is
    refused, so the child's own run ends `refused`.
    """
    child_that_acts(configs)
    parent_config(configs, inherit=False)
    run_parent(configs, tmp_path)

    assert child_status(tmp_path) == "refused", "the child acted without a token"


def test_a_configuration_can_declare_inheritance(configs: Path, tmp_path: Path) -> None:
    """Off by default, on by declaration — the same shape as every other gate here: the default
    is closed, and opening it is explicit and visible.

    The *same* child, the same scripted call, and the only difference is one line in a
    configuration. That is what makes inheritance a decision a reviewer can see.
    """
    child_that_acts(configs)
    parent_config(configs, inherit=True)
    run_parent(configs, tmp_path)

    assert child_status(tmp_path) == "ok", "inheritance did not reach the child"


# -------------------------------------------------------------------- the gate


def test_spawning_is_itself_gated(configs: Path, tmp_path: Path) -> None:
    """`spawn_agent` is a side effect, so the caller must authorise delegating at all.

    This is a different question from the child's own token, and both are needed: this one
    authorises *delegating*, the child's authorises *acting*.
    """
    parent_config(configs)
    output = run_parent(configs, tmp_path, token=None)

    assert output.status == "refused"
    assert output.failures[0].guardrail == "confirmation_missing"
    assert output.tool_calls == [] or output.tool_calls[0].outcome != "ok"


def test_the_tool_declares_itself_a_side_effect() -> None:
    """And the registry agrees, which is why this could not be declared idempotent."""
    assert SpawnAgentTool.side_effect is True
    assert SpawnAgentTool.idempotent is False


def test_the_model_never_sees_the_confirmation_field() -> None:
    advertised = SpawnAgentTool(runner=None).describe()
    assert "confirmation_token" not in advertised.parameters.get("properties", {})
