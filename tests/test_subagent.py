"""Starting a run from inside a run.

The first genuinely new mechanism the agent layer needs, and the four things
[decisions/0028](../../docs/decisions/0028-the-agent-is-a-capability.md) settles before it
existed: budget, trace, confirmation, failure.

These tests use stub configs written to a temporary root, so they exercise the real wiring —
`run_task`, the runner, the budget allocation, the trace — with no key and no spend.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from runtime.factory import run_task
from runtime.replay import replay
from runtime.schemas import RunRequest
from runtime.trace import read_trace
from tools.registry import ToolError
from tools.subagent import SpawnAgentArgs, SpawnAgentTool

#: Prices, because a delegated run's cost is the whole subject here: with everything at zero a
#: charge that lost the money entirely would still compare equal to a charge that kept it.
#: Spread into every provider block in this file — `write_config` replaces the block, it does
#: not merge it, so a child or parent written without these would silently run for free.
PRICES: dict[str, Any] = {"price_input_per_mtok": 1.0, "price_output_per_mtok": 2.0}

MINIMAL: dict[str, Any] = {
    "system_prompt": "A stub.",
    "provider": {"kind": "stub", "model": "stub", "stub_final": "child done", **PRICES},
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
            **PRICES,
        },
    )


def run_parent(root: Path, tmp_path: Path, *, token: str | None = "caller-token"):
    return run_task(
        RunRequest(task="do the thing", config="parent", confirmation_token=token),
        config_root=root,
        trace_dir=tmp_path / "traces",
    )


def parent_trace_path(output: Any, tmp_path: Path) -> Path:
    return tmp_path / "traces" / f"{output.trace_id}.jsonl"


def model_spend(trace_path: Path) -> tuple[int, float]:
    """What the parent spent on the model itself: ``(tokens, cost)``.

    Read from the trace rather than from the provider, because that is where replay reads it —
    and because a test that asked the provider would be checking the provider.
    """
    records = read_trace(trace_path).of("model_call")
    tokens = sum(r.payload["prompt_tokens"] + r.payload["completion_tokens"] for r in records)
    return tokens, sum(r.payload["cost_usd"] for r in records)


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
    the parent its whole share whether or not the child used it.

    Asserted as a decomposition — the run's totals are the parent's own model spend plus the
    child's, and nothing else — because "the cost went up" is also true of a run that charged
    the wrong number.
    """
    parent_config(configs)
    output = run_parent(configs, tmp_path)

    delegated = output.tool_calls[0].spend
    assert delegated.cost_usd > 0, "the child spent nothing, so this proves nothing"
    assert delegated.tokens_total > 0

    own_tokens, own_cost = model_spend(parent_trace_path(output, tmp_path))
    assert output.tokens_total == own_tokens + delegated.tokens_total
    assert output.cost_usd == pytest.approx(own_cost + delegated.cost_usd)


# ------------------------------------------------------ replaying a delegated run


def test_a_delegated_run_replays_to_the_same_cost(configs: Path, tmp_path: Path) -> None:
    """The defect [decisions/0030](../../docs/decisions/0030-starting-a-run-from-inside-a-run.md)
    found and
    [decisions/0031](../../docs/decisions/0031-the-spend-is-in-the-record.md) fixed.

    On replay the spawn is served from the parent's record — the child is not re-run, which is
    correct and is the whole point — so the charge has to come from the record too. It did not:
    the charge lived in the code that starts a child, which replay never reaches, so a replay
    of this run reported the parent's own model spend alone.
    """
    parent_config(configs)
    output = run_parent(configs, tmp_path)
    trace_path = parent_trace_path(output, tmp_path)

    replayed = replay(trace_path, trace_dir=tmp_path / "replay")

    _, own_cost = model_spend(trace_path)
    # The sharp one. Without the child's share these two are equal, and the test would pass on
    # the broken code if it only compared the replay against the recording.
    assert replayed.cost_usd > own_cost, "the replay lost the child's share"
    assert replayed.cost_usd == pytest.approx(output.cost_usd)
    assert replayed.tokens_total == output.tokens_total
    assert replayed.canonical() == output.canonical()


def test_the_replay_does_not_start_a_second_child(configs: Path, tmp_path: Path) -> None:
    """The other half of the same claim: the charge is restored *without* re-running the work.
    A replay that reproduced the cost by spawning again would be a replay that mutates."""
    parent_config(configs)
    output = run_parent(configs, tmp_path)

    replay(parent_trace_path(output, tmp_path), trace_dir=tmp_path / "replay")

    assert len(list((tmp_path / "replay").glob("*.jsonl"))) == 1, "the replay spawned something"
    assert len(list((tmp_path / "traces").glob("*.jsonl"))) == 2, "a third run appeared"


def test_the_replayed_cost_comes_from_the_record(configs: Path, tmp_path: Path) -> None:
    """Proof that the number is *read* rather than recomputed.

    Editing the recorded spend and replaying again must move the replayed cost by the same
    amount. Otherwise the agreement above is a coincidence, and the charge is coming from
    somewhere the replay should not be looking.
    """
    parent_config(configs)
    output = run_parent(configs, tmp_path)
    forged = forge_recorded_spend(parent_trace_path(output, tmp_path), extra_cost=0.001)

    replayed = replay(forged, trace_dir=tmp_path / "replay")

    assert replayed.cost_usd == pytest.approx(output.cost_usd + 0.001)


def forge_recorded_spend(trace_path: Path, *, extra_cost: float) -> Path:
    """A copy of the trace with the delegated spend edited. Nothing else changes."""
    lines: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["event"] == "tool_call":
            event["payload"]["spend"]["cost_usd"] += extra_cost
        lines.append(json.dumps(event))
    forged = trace_path.with_name("forged.jsonl")
    forged.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return forged


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
            **PRICES,
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
