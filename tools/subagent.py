"""Start a run from inside a run.

The first genuinely new mechanism the agent layer needs, and the one that makes every other
question concrete: a spawned run needs a budget, a trace, a confirmation story, and a failure
story. [decisions/0028](../../docs/decisions/0028-the-agent-is-a-capability.md) settles all four
before this file existed; this is the mechanical part.

**The tool does not start the run.** It cannot: `runtime/` is where runs are started, and a tool
importing the loop would invert the layering that keeps the core general. So the tool takes a
`SpawnRunner` — a callable injected by the composition root — and the core never learns that
`spawn_agent` exists. The delete test enforces that, not a convention.

Four things this file is careful about:

* **The child does not get the parent's budget.** It gets a *share of what is left*, and the
  parent is charged what the child actually spent — reported here as a `Spend` on the result,
  or on the error when the child did not finish, because a failed delegation is not a free
  one. `Budget.allocate` is where the share comes from; the *charge* happens in the loop, from
  the record, because that is the only place it also happens on replay.
* **The child does not get the caller's confirmation token** unless the configuration says so.
  The gate exists so the *principal* decides, and a sub-agent's caller is the parent — which is
  not the principal, and did not see the task decomposed.
* **A child's failure is a tool failure.** It raises `ToolError`, so the existing taxonomy handles
  it: `optional: false` makes the parent `partial`, optional makes it `degraded`. No new class.
* **`idempotent = False`.** A spawn costs money and may have spent some of it before failing, so
  it is never retried. That is not a limitation; it is the only safe answer.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from runtime.schemas import SpawnRequest, SpawnResult, SpawnRunner, ToolResult
from tools.registry import Tool, ToolError


class SpawnAgentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(
        description=(
            "What the spawned agent should do, stated as a complete task. It cannot see this "
            "conversation, so anything it needs must be in this string."
        ),
        min_length=1,
        max_length=4000,
    )
    #: Required, because spawning is a side effect — see the class docstring.
    confirmation_token: str = Field(description="Supplied by the caller. Never by the model.")


class SpawnAgentTool(Tool):
    """Run a separate agent on a sub-task and return what it reported."""

    name = "spawn_agent"
    description = (
        "Run a separate agent on a sub-task and return its answer. The agent cannot see this "
        "conversation, so the task must be self-contained. Use it to work on something in "
        "parallel with, or independently of, what you are doing — not to ask a question you "
        "could answer from what you already have."
    )
    args_model = SpawnAgentArgs
    #: **Spawning is a side effect, and I had this wrong first.**
    #:
    #: My instinct was `side_effect=False, idempotent=False` — nothing in the world changes,
    #: but a retry runs a second child. The registry refused it: *"a tool with no side effect is
    #: idempotent by definition"*. And the registry is right, for a reason worth keeping.
    #:
    #: Delegating spends the caller's budget on a task the caller did not specify. That is an
    #: action on their behalf, and the project's stance is that the *principal* decides. The
    #: budget bounds the damage; the gate is what makes it their choice.
    #:
    #: Note the two gates are different things and both are needed. This one authorises
    #: *delegating*. The child's own token — not inherited by default — authorises *acting*.
    side_effect = True
    idempotent = False
    optional = True
    timeout_s: ClassVar[float] = 600.0

    #: Which runtime facilities the composition root must inject, by *parameter* name.
    #:
    #: This is how the core supplies a runner without ever naming this tool. The catalogue sees
    #: "this factory wants a `runner`" and passes one; it does not see "spawn_agent". A new tool
    #: that needs a facility declares it here, and neither the core nor the seam changes.
    needs: ClassVar[frozenset[str]] = frozenset({"runner"})

    def __init__(
        self,
        *,
        runner: SpawnRunner | None = None,
        config: str | None = None,
        share: float = 0.25,
        inherit_confirmation: bool = False,
        max_depth: int = 1,
    ) -> None:
        """``runner`` comes from the composition root; everything else from `tool_options`.

        ``share`` and ``inherit_confirmation`` are configuration, not arguments — the model does
        not get to decide how much budget a child may spend, or whether it may act without the
        principal.
        """
        if not 0.0 < share <= 1.0:
            raise ValueError(f"share must be in (0, 1], got {share}")
        if max_depth < 0:
            raise ValueError(f"max_depth cannot be negative, got {max_depth}")
        self._runner = runner
        self._config = config
        self._share = share
        self._inherit_confirmation = inherit_confirmation
        self._max_depth = max_depth

    def invoke(self, args: BaseModel) -> ToolResult:
        assert isinstance(args, SpawnAgentArgs)
        if self._runner is None:
            raise ToolError(
                "spawn_agent was configured but no runner was wired in. A tool cannot start a "
                "run by itself — the composition root supplies the runner. If you are seeing "
                "this in a test, pass one; if in a run, the entry point did not wire it."
            )
        if self._max_depth <= 0:
            raise ToolError(
                "spawn_agent is at its depth limit: this run is already a spawned run, and "
                "spawning is not recursive without a configuration that says so."
            )

        result = self._runner(
            SpawnRequest(
                task=args.task,
                config=self._config,
                share=self._share,
                inherit_confirmation=self._inherit_confirmation,
            )
        )
        #: Reported on **both** outcomes. A child that came back `partial` still spent what it
        #: spent, and a record that says otherwise understates the parent — which is the defect
        #: decisions/0031 exists to close, one hop further along. This is also why the spend
        #: travels as a `Spend` rather than as two loose numbers: it has to survive three hops
        #: (child budget → result → tool record → trace) without being re-assembled on the way.
        spend = result.spend
        if result.status in {"failed", "refused", "partial"}:
            # A child that did not finish is a tool that did not succeed. Raised rather than
            # returned so the existing taxonomy decides what it means for the parent — which is
            # the whole point of decisions/0028's fourth answer.
            raise ToolError(
                f"the spawned run did not complete: status={result.status}"
                f"{f' reason={result.reason}' if result.reason else ''} "
                f"(trace {result.trace_id})",
                spend=spend,
            )
        return ToolResult(text=result.render(), spend=spend)


__all__ = ["SpawnAgentArgs", "SpawnAgentTool", "SpawnRequest", "SpawnResult", "SpawnRunner"]
