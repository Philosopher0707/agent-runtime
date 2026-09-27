"""The CI contract: one definition of the gates, and it is the Makefile's.

The spec says *"until `make eval` gates CI, no change may be claimed as an improvement."*
That makes CI the thing every other claim rests on, so CI itself has to be checkable
rather than trusted. Two failure modes are worth a test:

* **Drift.** If the workflow runs gates directly instead of calling `make ci`, a local run
  and CI can disagree, and "it passed" stops meaning anything.
* **Silent omission.** If a gate is dropped from the `ci` recipe, CI keeps passing while
  covering less. The recipe is parsed here and the gate set asserted, so dropping one is a
  test failure rather than a quiet reduction in coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: The gates, as the spec's definition of done names them, plus the two this project added.
EXPECTED_GATES = {"lock-check", "check", "eval", "markers", "smoke"}

#: Strings that would mean the workflow is running a gate itself rather than delegating.
GATE_INVOCATIONS = ("pytest", "ruff", "evals.score", "measure_markers", "smoke.py", "uv run")


def makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def ci_recipe() -> list[str]:
    """The shell lines of the `ci` target, in order."""
    lines = makefile_text().splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.startswith("ci:"))
    except StopIteration:  # pragma: no cover - only if the target is deleted
        pytest.fail("the Makefile has no `ci` target")
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.strip())
    return recipe


def makefile_targets() -> set[str]:
    return set(re.findall(r"^([a-zA-Z][a-zA-Z0-9_-]*):", makefile_text(), re.MULTILINE))


def workflow_run_steps() -> list[str]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps: list[str] = []
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step:
                steps.append(step["run"])
    return steps


# ------------------------------------------------------------------- the workflow


def test_the_workflow_exists() -> None:
    assert WORKFLOW.is_file(), "CI does not exist, so no change can be claimed as an improvement"


def test_the_workflow_is_valid_yaml() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert document.get("jobs")


def test_the_workflow_calls_make_ci() -> None:
    assert any("make ci" in step for step in workflow_run_steps()), (
        "the workflow does not run `make ci`, so CI and a local run are different things"
    )


@pytest.mark.parametrize("invocation", GATE_INVOCATIONS)
def test_the_workflow_does_not_run_gates_directly(invocation: str) -> None:
    """Delegation is the point: one definition of the gates, in the Makefile."""
    offenders = [step for step in workflow_run_steps() if invocation in step]
    assert not offenders, (
        f"the workflow invokes {invocation!r} directly: {offenders}. Move the gate into the "
        f"Makefile and call it from `make ci`."
    )


def test_the_workflow_installs_from_the_lockfile() -> None:
    """--frozen makes a stale uv.lock a CI failure rather than a silent re-resolve."""
    install_steps = [step for step in workflow_run_steps() if "uv sync" in step]
    assert install_steps, "the workflow never installs dependencies"
    assert all("--frozen" in step for step in install_steps), (
        f"the workflow syncs without --frozen: {install_steps}"
    )


def test_the_workflow_runs_on_pushes_and_pull_requests() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare `on:` key as the boolean True, which is why this falls back.
    triggers = document.get("on") or document.get(True)
    assert triggers, "the workflow has no triggers"
    assert {"push", "pull_request"} <= set(triggers)


# -------------------------------------------------------------------- the gates


def test_make_ci_runs_exactly_the_expected_gates() -> None:
    gates = [
        match.group(1)
        for line in ci_recipe()
        if (match := re.search(r"--no-print-directory\s+(\S+)", line))
    ]
    assert set(gates) == EXPECTED_GATES, (
        f"the ci recipe runs {sorted(gates)}, expected {sorted(EXPECTED_GATES)}. "
        f"A gate that is not in this recipe is not gated."
    )
    assert len(gates) == len(set(gates)), f"a gate is run twice: {gates}"


def test_every_gate_in_the_recipe_is_a_real_target() -> None:
    gates = {
        match.group(1)
        for line in ci_recipe()
        if (match := re.search(r"--no-print-directory\s+(\S+)", line))
    }
    unknown = gates - makefile_targets()
    assert not unknown, f"the ci recipe calls targets that do not exist: {sorted(unknown)}"


def test_the_gates_are_run_sequentially() -> None:
    """`ci` must stop at the first failure, not run everything and report at the end."""
    recipe = ci_recipe()

    def is_gate(line: str) -> bool:
        return "--no-print-directory" in line

    def is_echo(line: str) -> bool:
        return line.lstrip("@").startswith("echo")

    assert all(is_gate(line) or is_echo(line) for line in recipe), (
        f"the ci recipe contains a line that is neither a gate nor an echo: {recipe}"
    )
    assert not any("-k" in line or "|| true" in line for line in recipe), (
        "the ci recipe is allowed to continue past a failure"
    )


def test_the_specs_named_gates_are_all_present() -> None:
    """The spec names `make check`, `make eval` and `make smoke` explicitly."""
    gates = {
        match.group(1)
        for line in ci_recipe()
        if (match := re.search(r"--no-print-directory\s+(\S+)", line))
    }
    assert {"check", "eval", "smoke"} <= gates


def test_ci_is_declared_phony() -> None:
    """A file named `ci` in the repo root would otherwise make the target a no-op."""
    phony = re.search(r"^\.PHONY:(.*)$", makefile_text(), re.MULTILINE)
    assert phony is not None
    declared = set(phony.group(1).split())
    assert EXPECTED_GATES | {"ci"} <= declared, (
        f"not declared .PHONY: {sorted((EXPECTED_GATES | {'ci'}) - declared)}"
    )
