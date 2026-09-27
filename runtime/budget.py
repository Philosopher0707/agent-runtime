"""The only code that can abort a run.

Four bounds, all enforced in code, all configurable, none optional. There is no
path through the loop that does not pass through ``Budget.check``.

A budget never raises out of the runtime: it raises ``BudgetExceeded``, and the
loop's single job is to turn that into ``status=partial`` with the exhausted
limit named. Never hang, never raise, never silently continue.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from runtime.config import BudgetConfig


class BudgetExceeded(Exception):
    """A bound was reached. Carries which one, so the reason can name it."""

    def __init__(self, limit: str, detail: str) -> None:
        super().__init__(f"{limit} exhausted: {detail}")
        self.limit = limit
        self.detail = detail

    @property
    def reason(self) -> str:
        return f"budget_exhausted:{self.limit}"


@dataclass
class Budget:
    """Mutable run accounting. One instance per run; never shared.

    ``clock`` is injectable so wall-clock exhaustion can be forced in a test
    without sleeping.
    """

    max_steps: int
    max_tokens_total: int
    max_wall_clock_s: float
    max_cost_usd: float
    clock: Callable[[], float] = time.monotonic
    steps: int = 0
    tokens_total: int = 0
    cost_usd: float = 0.0
    started_at: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    @classmethod
    def from_config(
        cls,
        config: BudgetConfig,
        *,
        clock: Callable[[], float] | None = None,
    ) -> Budget:
        return cls(
            max_steps=config.max_steps,
            max_tokens_total=config.max_tokens_total,
            max_wall_clock_s=config.max_wall_clock_s,
            max_cost_usd=config.max_cost_usd,
            clock=clock or time.monotonic,
        )

    @property
    def elapsed_s(self) -> float:
        return max(0.0, self.clock() - self.started_at)

    def remaining_steps(self) -> int:
        return max(0, self.max_steps - self.steps)

    def check(self, *, include_steps: bool = True) -> None:
        """Raise if a bound is reached.

        ``include_steps=False`` is used when charging usage for a step that is already
        under way. The step bound decides whether a *new* step may begin; it must not
        retroactively invalidate a step that has already run, or a one-step budget could
        never complete its one step.
        """
        elapsed = self.elapsed_s
        if elapsed >= self.max_wall_clock_s:
            raise BudgetExceeded(
                "max_wall_clock_s", f"{elapsed:.3f}s elapsed of {self.max_wall_clock_s:.3f}s"
            )
        if include_steps and self.steps >= self.max_steps:
            raise BudgetExceeded("max_steps", f"{self.steps} of {self.max_steps} steps used")
        if self.tokens_total >= self.max_tokens_total:
            raise BudgetExceeded(
                "max_tokens_total", f"{self.tokens_total} of {self.max_tokens_total} tokens used"
            )
        if self.cost_usd >= self.max_cost_usd:
            raise BudgetExceeded(
                "max_cost_usd", f"${self.cost_usd:.6f} of ${self.max_cost_usd:.6f} spent"
            )

    def begin_step(self) -> int:
        """Claim a step index. Checks *before* claiming, so no step is unaffordable."""
        self.check()
        self.steps += 1
        return self.steps

    def add_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Charge a model call to the run, then re-check immediately.

        Re-checking here rather than only at the top of the next step is what stops
        a single expensive call from overshooting the bound unnoticed.
        """
        self.tokens_total += max(0, prompt_tokens) + max(0, completion_tokens)
        self.cost_usd += max(0.0, cost_usd)
        self.check(include_steps=False)


__all__ = ["Budget", "BudgetExceeded"]
