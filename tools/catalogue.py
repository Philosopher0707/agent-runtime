"""Wiring: a configuration's tool names to tool instances.

The only place tool implementations are named. A configuration names tools; this
module resolves those names; ``tools.registry`` enforces what a tool must be.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tools.builtin import BUILTIN_TOOLS, CONTROL_TOOL_NAMES
from tools.registry import Tool, ToolRegistry


class UnknownToolError(Exception):
    """A configuration named a tool that does not exist."""


def available_tool_names() -> list[str]:
    return sorted(BUILTIN_TOOLS)


def build_tool(name: str, **kwargs: Any) -> Tool:
    factory = BUILTIN_TOOLS.get(name)
    if factory is None:
        raise UnknownToolError(
            f"no such tool: {name!r} (available: {', '.join(available_tool_names())})"
        )
    return factory(**kwargs)


def build_registry(
    names: Iterable[str],
    *,
    extra: Iterable[Tool] = (),
    tool_kwargs: dict[str, dict[str, Any]] | None = None,
    facilities: dict[str, Any] | None = None,
    **registry_kwargs: Any,
) -> ToolRegistry:
    """Build a registry for a configuration.

    * Control tools are always present: they are protocol, not capability.
    * A tool in ``extra`` **replaces** a built-in of the same name. That is how the
      eval harness substitutes a scripted double for a real tool without editing the
      configuration under test.
    * ``facilities`` are runtime capabilities a tool may need — a way to start a run, say. A
      tool declares which it wants with a ``needs`` class attribute naming the *parameter*, and
      the facility is passed to any factory that asks. **The catalogue never learns a tool's
      name this way**, which is what lets the core supply a runner without the core knowing
      that `spawn_agent` exists.
    """
    tool_kwargs = tool_kwargs or {}
    facilities = facilities or {}
    overrides = list(extra)
    override_names = {tool.name for tool in overrides}

    tools: list[Tool] = [tool for tool in overrides if tool.name not in CONTROL_TOOL_NAMES]
    wanted = list(dict.fromkeys([*CONTROL_TOOL_NAMES, *names]))
    for name in wanted:
        if name in override_names:
            continue
        factory = BUILTIN_TOOLS.get(name)
        kwargs = dict(tool_kwargs.get(name, {}))
        if factory is not None:
            for facility in getattr(factory, "needs", ()):
                if facility in facilities:
                    kwargs[facility] = facilities[facility]
        tools.append(build_tool(name, **kwargs))

    return ToolRegistry(tools, **registry_kwargs)


__all__ = [
    "CONTROL_TOOL_NAMES",
    "UnknownToolError",
    "available_tool_names",
    "build_registry",
    "build_tool",
]
