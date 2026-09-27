"""The budget: four bounds, all enforced, none bypassable.

The point of these tests is that each bound is *forced* — not that the happy path
works. A bound with no test is undiscovered, not enforced.
"""

from __future__ import annotations

import pytest

from runtime.budget import Budget, BudgetExceeded
from runtime.config import BudgetConfig
from tests.helpers import FakeClock, execute, make_config, text


def make_budget(**overrides: float) -> Budget:
    values: dict[str, float] = {
        "max_steps": 5,
        "max_tokens_total": 1000,
        "max_wall_clock_s": 10.0,
        "max_cost_usd": 1.0,
    }
    values.update(overrides)
    return Budget(**values)  # type: ignore[arg-type]


def test_steps_bound_is_forced() -> None:
    budget = make_budget(max_steps=2)
    budget.begin_step()
    budget.begin_step()
    with pytest.raises(BudgetExceeded) as caught:
        budget.begin_step()
    assert caught.value.limit == "max_steps"
    assert caught.value.reason == "budget_exhausted:max_steps"


def test_zero_step_bound_terminates_immediately() -> None:
    """Zero is a legitimate way to say "start nothing". It must not hang."""
    budget = make_budget(max_steps=0)
    with pytest.raises(BudgetExceeded) as caught:
        budget.begin_step()
    assert caught.value.limit == "max_steps"


def test_token_bound_is_forced() -> None:
    budget = make_budget(max_tokens_total=10)
    budget.begin_step()
    with pytest.raises(BudgetExceeded) as caught:
        budget.add_usage(prompt_tokens=6, completion_tokens=6)
    assert caught.value.limit == "max_tokens_total"


def test_cost_bound_is_forced() -> None:
    budget = make_budget(max_cost_usd=0.001)
    budget.begin_step()
    with pytest.raises(BudgetExceeded) as caught:
        budget.add_usage(cost_usd=0.01)
    assert caught.value.limit == "max_cost_usd"


def test_wall_clock_bound_is_forced() -> None:
    clock = FakeClock()
    budget = Budget(
        max_steps=5,
        max_tokens_total=1000,
        max_wall_clock_s=10.0,
        max_cost_usd=1.0,
        clock=clock,
    )
    clock.advance(11.0)
    with pytest.raises(BudgetExceeded) as caught:
        budget.check()
    assert caught.value.limit == "max_wall_clock_s"


def test_usage_does_not_apply_the_step_bound() -> None:
    """Regression: a step that has already run must not be invalidated by the step bound.

    Charging usage re-checks the budget, but only the bounds that usage can affect.
    Applying ``max_steps`` here would mean a one-step budget could never finish its one
    step — which is exactly the bug this test exists to prevent.
    """
    budget = make_budget(max_steps=1)
    budget.begin_step()
    budget.add_usage(prompt_tokens=5, completion_tokens=5)  # must not raise
    with pytest.raises(BudgetExceeded, match="max_steps"):
        budget.begin_step()


def test_elapsed_is_never_negative() -> None:
    clock = FakeClock(start=100.0)
    budget = Budget(
        max_steps=5,
        max_tokens_total=1000,
        max_wall_clock_s=10.0,
        max_cost_usd=1.0,
        clock=clock,
    )
    clock.now = 50.0  # a clock that goes backwards
    assert budget.elapsed_s == 0.0


def test_from_config_carries_all_four_bounds() -> None:
    config = BudgetConfig(max_steps=3, max_tokens_total=99, max_wall_clock_s=1.5, max_cost_usd=0.25)
    budget = Budget.from_config(config)
    assert (budget.max_steps, budget.max_tokens_total) == (3, 99)
    assert (budget.max_wall_clock_s, budget.max_cost_usd) == (1.5, 0.25)


def test_loop_reports_partial_when_steps_run_out(tracer) -> None:
    """A run that needs a second step does not get one."""
    config = make_config(budget={"max_steps": 1})
    output = execute(
        "go",
        config=config,
        tracer=tracer,
        script=[{"tool_calls": [{"name": "echo", "arguments": {"text": "again"}}]}, text("done")],
    )
    assert output.status == "partial"
    assert output.reason == "budget_exhausted:max_steps"
    assert output.steps == 1


def test_a_run_that_fits_its_step_bound_is_not_partial(tracer) -> None:
    """The step bound is a ceiling, not a quota. Using fewer steps is not a failure."""
    config = make_config(budget={"max_steps": 1})
    output = execute("go", config=config, tracer=tracer, script=[text("done")])
    assert output.status == "ok"
    assert output.steps == 1


def test_loop_never_exceeds_the_step_bound(tracer) -> None:
    """A model that would loop forever is stopped by the budget, not by luck."""
    config = make_config(budget={"max_steps": 4})
    script = [{"tool_calls": [{"name": "echo", "arguments": {"text": "again"}}]} for _ in range(50)]
    output = execute("go", config=config, tracer=tracer, script=script, default_final="stopped")
    assert output.steps <= 4
    assert output.status == "partial"
    assert output.model_calls <= 4
