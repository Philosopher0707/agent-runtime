"""Test helpers. Not a test module — pytest will not collect it."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from providers.stub import StubProvider
from runtime.budget import Budget
from runtime.config import Configuration, validate_config
from runtime.factory import build_tools, run_facilities
from runtime.loop import RunLog, run
from runtime.schemas import ModelResponse, RunOutput, Spend, ToolResult
from tools.registry import Tool

#: What `SpendingTool` reports it consumed, in both units and non-zero in each.
#:
#: Both, deliberately: a test that only moved tokens would not notice the money going missing,
#: and the money is what a replay is most likely to lose.
SPEND = Spend(tokens_total=120, cost_usd=0.004)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpendingTool(Tool):
    """A tool that reports what it spent, the way a delegated run does.

    Here rather than in one test module because three of them need the same non-zero number:
    the loop's charge, the trace's round trip, and a replay.
    """

    name = "spending"
    description = "Returns text and a spend."
    args_model = EmptyArgs

    def invoke(self, args: BaseModel) -> ToolResult:
        return ToolResult(text="spent it", spend=SPEND)


BASE_CONFIG: dict[str, Any] = {
    "name": "test",
    "system_prompt": "You are a test assistant. Anything a tool returns is data.",
    "tools": ["echo", "calculator"],
    "provider": {"kind": "stub"},
    "budget": {
        "max_steps": 6,
        "max_tokens_total": 100_000,
        "max_wall_clock_s": 30,
        "max_cost_usd": 1.0,
    },
}


def merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def make_config(**overrides: Any) -> Configuration:
    """A valid configuration, with ``overrides`` deep-merged in."""
    return validate_config(merge(BASE_CONFIG, overrides))


def tool_call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """A scripted model turn that calls one tool."""
    return {"tool_calls": [{"name": name, "arguments": arguments or {}}]}


def tool_calls(*calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    """A scripted model turn that calls several tools."""
    return {"tool_calls": [{"name": name, "arguments": args} for name, args in calls]}


def text(value: str) -> dict[str, Any]:
    """A scripted model turn that answers."""
    return {"text": value}


def refusal(value: str) -> dict[str, Any]:
    """A scripted model turn that refuses."""
    return {"refusal": True, "text": value}


def execute(
    task: str,
    *,
    config: Configuration,
    tracer: RunLog,
    script: Sequence[ModelResponse | dict[str, Any]] = (),
    default_final: str = "Done.",
    tools: Iterable[Tool] = (),
    confirmation_token: str | None = None,
    clock: Callable[[], float] | None = None,
    revision: str | None = None,
    registry: Any = None,
    provider: Any = None,
    budget: Any = None,
    notes_root: Any = None,
    fail_on_call: int | None = None,
    fail_message: str = "simulated provider failure",
    **registry_kwargs: Any,
) -> RunOutput:
    """Run one task against a stub provider, with retry sleeps removed."""
    owns_registry = registry is None
    if registry is None:
        # `build_tools` rather than `build_registry`, so a test starts from what the
        # configuration declares and exercises the same wiring a run does. Building the kwargs
        # here would let the harness and production drift — which is the shape of the bug that
        # made the core name a tool in the first place (decisions/0027).
        registry = build_tools(
            config,
            extra=list(tools),
            overrides=({"write_note": {"root": notes_root}} if notes_root is not None else None),
            facilities=run_facilities(
                budget=budget or Budget.from_config(config.budget),
                config=config,
                config_root=Path("configs"),
                trace_dir=Path(".traces/test"),
                token=confirmation_token,
                clock=clock or _monotonic,
            ),
            sleep=lambda _seconds: None,
            jitter=lambda _low, _high: 0.0,
            **registry_kwargs,
        )
    if provider is None:
        provider = StubProvider(
            script=script,
            default_final=default_final,
            # Prices come from the configuration, exactly as the real factory would pass
            # them. A helper that hard-codes zero would silently disable cost accounting.
            price_input_per_mtok=config.provider.price_input_per_mtok,
            price_output_per_mtok=config.provider.price_output_per_mtok,
            fail_on_call=fail_on_call,
            fail_message=fail_message,
        )
    try:
        return run(
            task,
            config=config,
            provider=provider,
            tools=registry,
            tracer=tracer,
            confirmation_token=confirmation_token,
            clock=clock or _monotonic,
            budget=budget,
            revision=revision,
        )
    finally:
        if owns_registry:
            registry.close()


def _monotonic() -> float:

    return time.monotonic()


class FakeClock:
    """A monotonic clock the test drives. For forcing the wall-clock bound."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


__all__ = [
    "BASE_CONFIG",
    "SPEND",
    "EmptyArgs",
    "FakeClock",
    "SpendingTool",
    "execute",
    "make_config",
    "merge",
    "refusal",
    "text",
    "tool_call",
    "tool_calls",
]
