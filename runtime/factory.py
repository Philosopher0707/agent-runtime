"""The composition root: the only place concrete adapters are named.

Everything upstream of here depends on a protocol. This module is where the protocols
meet implementations, and it is deliberately the only file that does so — which is why
"the loop contains no provider-specific code" is checkable by grepping one directory
rather than by reading every file.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path

from providers.base import Provider
from providers.openai_compat import OpenAICompatProvider
from providers.stub import StubProvider
from runtime.config import (
    DEFAULT_CONFIG_DIR,
    ConfigError,
    Configuration,
    ProviderConfig,
    load_config_by_name,
    read_api_key,
)
from runtime.loop import run
from runtime.schemas import RunOutput, RunRequest
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
    notes_root: str | Path = DEFAULT_NOTES_ROOT,
    **registry_kwargs: object,
) -> ToolRegistry:
    """Build the tool set a configuration names."""
    return build_registry(
        config.tools,
        extra=extra,
        tool_kwargs={"write_note": {"root": notes_root}},
        **registry_kwargs,  # type: ignore[arg-type]
    )


def run_task(
    request: RunRequest,
    *,
    config_root: str | Path = DEFAULT_CONFIG_DIR,
    trace_dir: str | Path = DEFAULT_TRACE_DIR,
    notes_root: str | Path = DEFAULT_NOTES_ROOT,
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
    registry = build_tools(config, extra=extra_tools, notes_root=notes_root, **registry_kwargs)
    trace_id = request.trace_id or new_trace_id()
    try:
        with TraceWriter(trace_dir, trace_id) as tracer:
            return run(
                request.task,
                config=config,
                provider=provider,
                tools=registry,
                tracer=tracer,
                confirmation_token=request.confirmation_token,
                clock=clock,
            )
    finally:
        registry.close()


__all__ = [
    "DEFAULT_NOTES_ROOT",
    "DEFAULT_TRACE_DIR",
    "build_provider",
    "build_tools",
    "run_task",
]
