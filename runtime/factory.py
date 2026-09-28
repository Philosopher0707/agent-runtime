"""The composition root: the only place concrete adapters are named.

Everything upstream of here depends on a protocol. This module is where the protocols
meet implementations, and it is deliberately the only file that does so — which is why
"the loop contains no provider-specific code" is checkable by grepping one directory
rather than by reading every file.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from providers.base import Provider
from providers.openai_compat import OpenAICompatProvider
from providers.stub import StubProvider
from runtime.budget import Budget
from runtime.config import (
    DEFAULT_CONFIG_DIR,
    ConfigError,
    Configuration,
    ProviderConfig,
    load_config_by_name,
    read_api_key,
)
from runtime.loop import run
from runtime.schemas import RunOutput, RunRequest, SpawnRequest, SpawnResult, SpawnRunner
from runtime.trace import TraceWriter, new_trace_id
from tools.catalogue import build_registry
from tools.registry import Tool, ToolRegistry

DEFAULT_TRACE_DIR = Path(".traces")
DEFAULT_NOTES_ROOT = Path(".notes")


def build_provider(config: ProviderConfig) -> Provider:
    """Construct the configured provider. The only dispatch on ``kind`` in the codebase."""
    if config.kind == "stub":
        return StubProvider(
            model=config.model,
            script=config.stub_script,
            default_final=config.stub_final,
            price_input_per_mtok=config.price_input_per_mtok,
            price_output_per_mtok=config.price_output_per_mtok,
        )
    if config.kind == "openai_compat":
        if not config.base_url:
            raise ConfigError("provider.kind is openai_compat but provider.base_url is unset")
        return OpenAICompatProvider(
            base_url=config.base_url,
            model=config.model,
            api_key=read_api_key(config),
            price_input_per_mtok=config.price_input_per_mtok,
            price_output_per_mtok=config.price_output_per_mtok,
            timeout_s=config.timeout_s,
        )
    raise ConfigError(f"unknown provider kind: {config.kind!r}")


def build_tools(
    config: Configuration,
    *,
    extra: Iterable[Tool] = (),
    overrides: dict[str, dict[str, Any]] | None = None,
    facilities: dict[str, Any] | None = None,
    **registry_kwargs: object,
) -> ToolRegistry:
    """Build the tool set a configuration names.

    The configuration declares what each of its tools is constructed with, in
    ``tool_options``. ``overrides`` lets a caller redirect one — a CLI flag, a per-case
    temporary directory — without the core knowing any tool's name.

    ``facilities`` are runtime capabilities a tool may need, passed by *parameter* name to any
    factory that declares it wants one. That is how this function supplies a way to start a run
    without ever naming the tool that uses it — see `tools/catalogue.py`.

    That distinction is the point. This function used to pass
    ``{"write_note": {"root": ...}}`` itself, so **only a tool that happened to be called
    `write_note` could ever receive an argument** — and the triage tools worked only because
    their default directory happened to be right. A capability could not configure its own
    tools, and the core named one anyway.
    """
    tool_kwargs = {name: dict(options) for name, options in config.tool_options.items()}
    for name, options in (overrides or {}).items():
        tool_kwargs.setdefault(name, {}).update(options)
    return build_registry(
        config.tools,
        extra=extra,
        tool_kwargs=tool_kwargs,
        facilities=facilities,
        **registry_kwargs,  # type: ignore[arg-type]
    )


def spawn_runner(
    *,
    budget: Budget,
    config_root: str | Path,
    trace_dir: str | Path,
    parent_config: str,
    parent_token: str | None,
    clock: Callable[[], float],
) -> SpawnRunner:
    """A callable a tool can use to start a run.

    Built here rather than in the tool, because a tool must not import the loop — and because
    the parent's live budget is the thing the allocation comes from, and only the composition
    root holds it.

    **A child gets no runner of its own.** That is what makes spawning one level deep: the
    child's tools are built without this facility, so its `spawn_agent` (if its configuration
    names one) has no way to start anything and refuses. Recursion is a decision nobody has
    needed to take, and this is the shape of not taking it by accident.
    """

    def run_spawn(request: SpawnRequest) -> SpawnResult:
        child_config = load_config_by_name(request.config or parent_config, root=config_root)
        child_budget = budget.allocate(request.share)
        provider = build_provider(child_config.provider)
        registry = build_tools(
            child_config,
            # No `facilities`: a spawned run cannot spawn.
            sleep=lambda _seconds: None,
            jitter=lambda _low, _high: 0.0,
        )
        trace_id = new_trace_id()
        try:
            with TraceWriter(trace_dir, trace_id) as tracer:
                output = run(
                    request.task,
                    config=child_config,
                    provider=provider,
                    tools=registry,
                    tracer=tracer,
                    budget=child_budget,
                    confirmation_token=parent_token if request.inherit_confirmation else None,
                    clock=clock,
                )
        finally:
            registry.close()
        # Charge what the child *spent*, not what it was allowed. Charging the allocation would
        # make a spawn cost the parent its whole share whether or not the child used it.
        budget.charge(child_budget)
        return SpawnResult(
            trace_id=trace_id,
            status=str(output.status),
            output=output.output,
            reason=output.reason,
            steps=output.steps,
            cost_usd=child_budget.cost_usd,
            tokens_total=child_budget.tokens_total,
        )

    return run_spawn


def run_facilities(
    *,
    budget: Budget,
    config: Configuration,
    config_root: str | Path,
    trace_dir: str | Path,
    token: str | None,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """The runtime facilities a tool may need, for one run.

    **Every entry point supplies these**, and that is the point of having a function rather than
    inlining it in `run_task`. A facility wired in one entry point and not another is a tool that
    works from the CLI and fails from the eval harness — which is exactly what happened: the
    first live sweep came back `degraded` because `spawn_agent` had no runner, and the only place
    that had wired one was `run_task`.

    A tool that needs no facility ignores this. A configuration that names no such tool is
    unaffected.
    """
    return {
        "runner": spawn_runner(
            budget=budget,
            config_root=config_root,
            trace_dir=trace_dir,
            parent_config=config.name,
            parent_token=token,
            clock=clock,
        )
    }


def run_task(
    request: RunRequest,
    *,
    config_root: str | Path = DEFAULT_CONFIG_DIR,
    trace_dir: str | Path = DEFAULT_TRACE_DIR,
    tool_overrides: dict[str, dict[str, Any]] | None = None,
    clock: Callable[[], float] = time.monotonic,
    extra_tools: Iterable[Tool] = (),
    **registry_kwargs: object,
) -> RunOutput:
    """Load a configuration, build the run, and return its output.

    The single entry point shared by the CLI and the HTTP service, so the two cannot
    drift into running different things.
    """
    config = load_config_by_name(request.config, root=config_root)
    provider = build_provider(config.provider)
    # The budget is built here rather than inside the loop, because a spawned run's allocation
    # comes out of it — and only the composition root holds the live instance.
    budget = Budget.from_config(config.budget, clock=clock)
    registry = build_tools(
        config,
        extra=extra_tools,
        overrides=tool_overrides,
        # Generic, by parameter name: the core supplies a way to start a run without ever
        # naming the tool that uses it. A configuration that names no such tool ignores this.
        facilities=run_facilities(
            budget=budget,
            config=config,
            config_root=config_root,
            trace_dir=trace_dir,
            token=request.confirmation_token,
            clock=clock,
        ),
        **registry_kwargs,
    )
    trace_id = request.trace_id or new_trace_id()
    try:
        with TraceWriter(trace_dir, trace_id) as tracer:
            return run(
                request.task,
                config=config,
                provider=provider,
                tools=registry,
                tracer=tracer,
                budget=budget,
                confirmation_token=request.confirmation_token,
                clock=clock,
                revision=current_revision(),
            )
    finally:
        registry.close()


@lru_cache(maxsize=4)
def current_revision(root: str | None = None) -> str | None:
    """Which build this is, as ``git describe --always --dirty``.

    Resolved at the composition root rather than in the loop: "which build am I" is a
    property of the *deployment*, not of a run, and the loop must not shell out. Cached,
    because a process has one revision — the one it started with.

    ``-dirty`` matters as much as the hash. A trace from a dirty tree does not correspond to
    any commit, so recording the hash alone would imply a reproducibility that is not there.

    Returns ``None`` outside a repository, which is not an error: this is a library and may
    be installed from a wheel with no git metadata at all. ``AGENT_REVISION`` overrides it,
    for a container that has the revision baked in at build time.
    """
    override = (os.environ.get("AGENT_REVISION") or "").strip()
    if override:
        return override
    try:
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,  # a hung git must not hang a run
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


__all__ = [
    "DEFAULT_NOTES_ROOT",
    "DEFAULT_TRACE_DIR",
    "build_provider",
    "build_tools",
    "current_revision",
    "run_task",
]
