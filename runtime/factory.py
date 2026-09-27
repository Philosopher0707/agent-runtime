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
