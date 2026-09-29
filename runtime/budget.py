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

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens_total - self.tokens_total)

    @property
    def remaining_cost_usd(self) -> float:
        return max(0.0, self.max_cost_usd - self.cost_usd)

    @property
    def remaining_wall_clock_s(self) -> float:
        return max(0.0, self.max_wall_clock_s - self.elapsed_s)

    def allocate(self, share: float) -> Budget:
        """A child's budget: a share of what is left, and not a penny more.

        **Not** "the child gets the parent's budget". That makes a declared bound multiply —
        the defect [decisions/0015](../../docs/decisions/0015-cost-budget-must-bind.md) refuses
        at the configuration level, arriving by the back door. A parent with $1 that hands $1 to
        each of three children has declared a $1 bound and spent $3.

        The allocation is computed from what remains *now*, so a parent that has already spent
        cannot allocate what it no longer has. Children run one at a time and each one's spend
        is charged back — by the parent's loop, from the record of the call that started it —
        which means a sequence of spawns cannot oversubscribe either: a *reservation* would be
        needed for concurrent children, and there are none.
        """
        if not 0.0 < share <= 1.0:
            raise ValueError(f"a budget share must be in (0, 1], got {share}")
        return Budget(
            max_steps=max(1, int(self.remaining_steps() * share)),
            max_tokens_total=max(1, int(self.remaining_tokens * share)),
            max_wall_clock_s=max(0.001, self.remaining_wall_clock_s * share),
            max_cost_usd=max(0.000001, self.remaining_cost_usd * share),
            clock=self.clock,
        )

    def charge(self, *, tokens_total: int = 0, cost_usd: float = 0.0) -> None:
        """Add a call's reported spend to this budget, then re-check.

        **Numbers, not a child `Budget`.** A delegated run's spend arrives here from the
        *record* of the tool call that started it, because on replay the child is never
        started and there is no child budget to charge from. That is the whole reason this
        signature is what it is
        ([decisions/0031](../../docs/decisions/0031-the-spend-is-in-the-record.md)); it is also
        why the charge happens in the loop rather than in whatever started the child.

        Re-checked immediately, like :meth:`add_usage`, so a spend larger than a bound is caught
        where it happened rather than one step later. The check runs even when the spend is
        zero: the bound is checked at every call, not only at the calls that happened to cost
        something, and a bound that is sometimes not checked is a bound that is sometimes not
        a bound.

        Charging the *usage* and not the allocation is the caller's job — the record holds what
        a call actually consumed. Charging an allocation would make a spawn cost the parent its
        whole share whether or not the child used it, which would make delegating expensive in
        a way nobody declared.
        """
        self.tokens_total += max(0, tokens_total)
        self.cost_usd += max(0.0, cost_usd)
        self.check(include_steps=False)

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
