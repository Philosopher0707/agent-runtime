"""A provider that answers from a recorded trace instead of from a model.

It also *checks*: every incoming prompt is hashed and compared against the hash the
trace recorded. A mismatch is raised, not smoothed over — the run and its replay
diverged, and that is a defect in the runtime's determinism, not a run outcome.
"""

from __future__ import annotations

from collections.abc import Sequence

from providers.base import prompt_hash
from runtime.schemas import ModelCallRecord, ModelResponse, ToolDescriptor


class ReplayDivergence(Exception):
    """A replay did not reproduce the run it came from.

    Deliberately not a ``ProviderError``: the loop would turn that into a run
    status, and a divergence must escape the loop rather than be reported as one
    more way a run can end.
    """


class ReplayProvider:
    name = "replay"

    def __init__(self, records: Sequence[ModelCallRecord], *, model: str | None = None) -> None:
        self._records = list(records)
        self.model = model or (self._records[0].model if self._records else "replay")
        self._index = 0

    @property
    def remaining(self) -> int:
        return max(0, len(self._records) - self._index)

    def health(self) -> bool:
        return True

    def complete(
        self,
        messages: list[dict[str, object]],
        tools: Sequence[ToolDescriptor],
    ) -> ModelResponse:
        if self._index >= len(self._records):
            raise ReplayDivergence(
                f"replay exhausted after {len(self._records)} recorded model call(s); "
                f"the run asked for one more. The recorded run took a different path."
            )
        record = self._records[self._index]
        actual = prompt_hash(messages, tools)  # type: ignore[arg-type]
        if actual != record.prompt_hash:
            raise ReplayDivergence(
                f"step {record.step}: the replayed prompt does not match the recorded one "
                f"(rebuilt {actual[:16]}..., recorded {record.prompt_hash[:16]}...). "
                f"Context assembly is not deterministic."
            )
        self._index += 1
        return record.response


__all__ = ["ReplayDivergence", "ReplayProvider"]
