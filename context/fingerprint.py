"""The prompt's identity, by component.

`prompt_hash` answers *whether* the prompt changed, and it is the authority on that: replay
verifies it, and nothing else does. What it cannot answer is **what** changed — the system
prompt, a tool description, the envelope preamble and the tool-call renderer all live inside
one hash, and they live in four different files, two of them Python source.

That is fine for a machine and useless for a person. This module names the parts.

    the system prompt   -> configs/<name>.yaml
    the tool schemas    -> tools/, via their descriptors
    the envelope        -> context/sanitize.py   (source, not content)
    the renderer        -> context/assembler.py  (source, not content)

Each gets its own digest, and the four together get an **identity** — a hash of the stable
parts, which is what a version number would name. The transcript is deliberately not part of
it: a prompt version should not change because a tool returned a different number.

This is the prerequisite for versioning a prompt at all. **You cannot version a composition
you cannot name**, and naming it is most of the human benefit: a change now reports *which*
component moved rather than "the hash differs".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from context import assembler
from context.sanitize import UNTRUSTED_CLOSE, UNTRUSTED_OPEN, UNTRUSTED_PREAMBLE
from runtime.schemas import ToolDescriptor


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: A call rendered purely to observe the renderer's *format*. Changing the format moves the
#: hash, which is the point — the format is inside every prompt and nothing else names it.
_RENDERER_PROBE = ("probe", {"probe": "probe"})


@dataclass(frozen=True)
class PromptFingerprint:
    """The stable parts of a prompt, each hashed, plus their combined identity."""

    system_prompt: str
    tool_definitions: str
    envelope: str
    renderer: str

    def as_dict(self) -> dict[str, str]:
        return {
            "system_prompt": self.system_prompt,
            "tool_definitions": self.tool_definitions,
            "envelope": self.envelope,
            "renderer": self.renderer,
            "identity": self.identity,
        }

    @property
    def identity(self) -> str:
        """One hash over the four component hashes.

        This is what a semantic version would *name*, and it is what to compare when asking
        "is this the same prompt as last time". It deliberately excludes the transcript: a
        prompt version should not move because a tool returned a different number.
        """
        return _digest(
            json.dumps(
                [
                    self.system_prompt,
                    self.tool_definitions,
                    self.envelope,
                    self.renderer,
                ],
                separators=(",", ":"),
            )
        )

    def changed_from(self, other: PromptFingerprint) -> list[str]:
        """Which components differ. The diagnosis a bare hash cannot give."""
        return [
            name
            for name in ("system_prompt", "tool_definitions", "envelope", "renderer")
            if getattr(self, name) != getattr(other, name)
        ]


def fingerprint(
    *,
    system_prompt: str,
    tools: list[ToolDescriptor] | tuple[ToolDescriptor, ...] = (),
) -> PromptFingerprint:
    """Hash the stable parts of the prompt a configuration would send."""
    definitions: list[dict[str, Any]] = sorted(
        (tool.model_dump(mode="json") for tool in tools),
        key=lambda item: item["name"],
    )
    envelope = "\n".join((UNTRUSTED_OPEN, UNTRUSTED_PREAMBLE, UNTRUSTED_CLOSE))
    return PromptFingerprint(
        system_prompt=_digest(system_prompt),
        tool_definitions=_digest(json.dumps(definitions, sort_keys=True, separators=(",", ":"))),
        envelope=_digest(envelope),
        renderer=_digest(assembler.render_tool_call(*_RENDERER_PROBE)),
    )


__all__ = ["PromptFingerprint", "fingerprint"]
