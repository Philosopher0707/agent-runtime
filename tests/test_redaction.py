"""Redaction: what must be scrubbed before logging, and what must not be touched.

Two claims are load-bearing here, and they pull in opposite directions:

1. **The trace is scrubbed.** No pattern-shaped personal data reaches the file on disk.
2. **The prompt is not.** The model still sees the real values, because redacting the
   prompt would silently change the task.

And one consequence that has to be faced rather than hidden: **a redacted trace cannot be
replayed.** Replay rebuilds each prompt from the trace and compares hashes, so removing the
text removes the ability to reproduce the run. That is asserted, not glossed.
"""

from __future__ import annotations

import pytest

from providers.stub import StubProvider
from runtime.config import ConfigError, validate_config
from runtime.redact import (
    DEFAULT_PATTERNS,
    PATTERN_NAMES,
    RedactionCounts,
    RedactionError,
    Redactor,
)
from runtime.replay import ReplayDivergence, ReplayUnavailable, replay
from runtime.schemas import RunOutput
from runtime.trace import TraceError, TraceWriter, new_trace_id, read_trace
from tests.helpers import execute, make_config, text, tool_call
from tools.scripted import ScriptedTool

EMAIL = "dana@example.com"
PHONE = "555-123-4567"
CARD = "4111 1111 1111 1111"
SSN = "123-45-6789"
KEY = "gho_abcdefghijklmnopqrstuvwxyz0123456789"


# ------------------------------------------------------------------- the patterns


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("email", EMAIL),
        ("email", "first.last+tag@sub.example.co.uk"),
        ("phone", "+44 20 7946 0958"),
        ("phone", "(020) 7946 0958"),
        ("phone", PHONE),
        ("credit_card", CARD),
        ("credit_card", "4111111111111111"),
        ("national_id", SSN),
        ("api_key", KEY),
        ("api_key", "sk-" + "a" * 24),
    ],
)
def test_each_pattern_redacts_its_shape(name: str, value: str) -> None:
    redactor = Redactor.from_names([name])
    redacted, counts = redactor.text(value)
    assert value not in redacted
    assert counts.counts == {name: 1}
    assert f"[redacted:{name}]" in redacted


@pytest.mark.parametrize(
    "innocent",
    [
        "2026-09-27",
        "2026-09-27T10:31:40Z",
        "release 1.2.3",
        "trace 41cb98e7",
        "steps: 2, tokens: 803",
        "the number 42",
        "3-3-4 is a grouping, not a number",
        "version 2026.09.27.1",
    ],
)
def test_ordinary_values_are_not_redacted(innocent: str) -> None:
    """Over-matching is the acceptable error here — but a *date* is not over-matching.

    A redactor that ate every ISO timestamp would make a trace useless for the one thing
    traces are for. The phone pattern is shaped to require a '+', parentheses, or 3-3-4
    grouping for exactly this reason.
    """
    redactor = Redactor.from_names(DEFAULT_PATTERNS)
    redacted, counts = redactor.text(innocent)
    assert redacted == innocent
    assert counts.total == 0


def test_the_pattern_set_is_the_documented_one() -> None:
    assert {"email", "phone", "credit_card", "national_id", "api_key"} == PATTERN_NAMES
    assert set(DEFAULT_PATTERNS) == PATTERN_NAMES


def test_every_pattern_says_what_it_catches() -> None:
    for pattern in Redactor.from_names(DEFAULT_PATTERNS).patterns:
        assert pattern.describes.strip(), f"{pattern.name} has no description"


# ------------------------------------------------------------------- the redactor


def test_nested_payloads_are_walked() -> None:
    redactor = Redactor.from_names(["email"])
    payload = {
        "outer": [{"inner": f"write to {EMAIL}"}],
        "tuple": (EMAIL, 1, None),
    }
    redacted, counts = redactor.value(payload)
    assert EMAIL not in str(redacted)
    assert redacted["outer"][0]["inner"] == "write to [redacted:email]"
    assert redacted["tuple"][0] == "[redacted:email]"
    assert counts.total == 2


def test_mapping_keys_are_redacted_too() -> None:
    """A tool that returns a mapping keyed by an email address is not hypothetical."""
    redactor = Redactor.from_names(["email"])
    redacted, _ = redactor.value({EMAIL: "a customer"})
    assert list(redacted) == ["[redacted:email]"]


def test_non_string_leaves_pass_through() -> None:
    redactor = Redactor.from_names(DEFAULT_PATTERNS)
    redacted, counts = redactor.value({"n": 1, "f": 1.5, "b": True, "z": None})
    assert redacted == {"n": 1, "f": 1.5, "b": True, "z": None}
    assert counts.total == 0


def test_redaction_is_idempotent() -> None:
    """The placeholder carries no personal data, so a second pass is a no-op."""
    redactor = Redactor.from_names(DEFAULT_PATTERNS)
    once, _ = redactor.text(f"{EMAIL} {PHONE} {CARD} {SSN} {KEY}")
    twice, counts = redactor.text(once)
    assert twice == once
    assert counts.total == 0


def test_counts_accumulate_across_calls() -> None:
    redactor = Redactor.from_names(["email"])
    counts = RedactionCounts()
    for _ in range(3):
        _, seen = redactor.text(EMAIL)
        counts.merge(seen)
    assert counts.counts == {"email": 3}
    assert counts.as_dict() == {"total": 3, "by_pattern": {"email": 3}}


def test_an_unknown_pattern_name_is_refused() -> None:
    with pytest.raises(RedactionError, match="unknown redaction pattern"):
        Redactor.from_names(["email", "mothers_maiden_name"])


def test_an_empty_pattern_set_is_refused() -> None:
    """A redactor that redacts nothing is a promise with no mechanism behind it."""
    with pytest.raises(RedactionError, match="at least one pattern"):
        Redactor(())


# --------------------------------------------------------------------- the config


def test_redaction_is_off_by_default() -> None:
    """Off by default is a deliberate trade, not an omission: it keeps replay possible."""
    config = make_config()
    assert config.guardrails.redaction.mode == "off"
    assert set(config.guardrails.redaction.patterns) == PATTERN_NAMES


def test_an_unknown_pattern_in_a_config_is_refused() -> None:
    with pytest.raises(ConfigError, match="unknown redaction pattern"):
        validate_config(
            {
                "name": "bad",
                "system_prompt": "s",
                "provider": {"kind": "stub"},
                "budget": {
                    "max_steps": 1,
                    "max_tokens_total": 1,
                    "max_wall_clock_s": 1,
                    "max_cost_usd": 1,
                },
                "guardrails": {"redaction": {"mode": "trace", "patterns": ["nope"]}},
            }
        )


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ConfigError, match="mode"):
        validate_config(
            {
                "name": "bad",
                "system_prompt": "s",
                "provider": {"kind": "stub"},
                "budget": {
                    "max_steps": 1,
                    "max_tokens_total": 1,
                    "max_wall_clock_s": 1,
                    "max_cost_usd": 1,
                },
                "guardrails": {"redaction": {"mode": "everywhere"}},
            }
        )


# ------------------------------------------------------------------ the trace boundary


def _run_with_pii(*, tracer, mode: str, provider=None) -> StubProvider:
    """A run whose task, tool arguments, tool result and answer all carry personal data."""
    config = make_config(guardrails={"redaction": {"mode": mode}})
    provider = provider or StubProvider(
        script=[tool_call("lookup", {"query": EMAIL}), text(f"Emailed {EMAIL}")],
        default_final="done",
    )
    execute(
        f"Look up {EMAIL}",
        config=config,
        tracer=tracer,
        provider=provider,
        tools=[ScriptedTool(name="lookup", result=f"Contact {EMAIL} or {PHONE}")],
    )
    return provider


def test_with_redaction_off_the_trace_is_faithful(tracer) -> None:
    _run_with_pii(tracer=tracer, mode="off")
    written = tracer.path.read_text(encoding="utf-8")
    assert EMAIL in written
    assert read_trace(tracer.path).first("redaction") is None


def test_with_redaction_on_no_personal_data_reaches_the_file(tracer) -> None:
    _run_with_pii(tracer=tracer, mode="trace")
    written = tracer.path.read_text(encoding="utf-8")
    for secret in (EMAIL, PHONE):
        assert secret not in written, f"{secret!r} survived into the trace"


def test_redaction_covers_every_event_kind(tracer) -> None:
    """The task, the arguments, the result, the answer and the failures all pass emit()."""
    _run_with_pii(tracer=tracer, mode="trace")
    written = tracer.path.read_text(encoding="utf-8")
    assert "[redacted:email]" in written
    for event in ("run_started", "tool_call", "model_call", "run_finished"):
        assert f'"{event}"' in written


def test_redaction_is_recorded_in_the_trace(tracer) -> None:
    """A redacted trace must say so, or a reader cannot tell what they are looking at."""
    _run_with_pii(tracer=tracer, mode="trace")
    summary = read_trace(tracer.path).first("redaction")
    assert summary is not None
    assert summary.payload["mode"] == "trace"
    assert summary.payload["total"] > 0
    assert summary.payload["by_pattern"]["email"] >= 1


def test_the_prompt_still_carries_the_real_values(tracer) -> None:
    """The claim that matters: redaction protects the record, not the model's input.

    If this ever fails, the runtime has started answering a different question than the
    one it was asked — which is worse than leaking, because it is silent.
    """
    provider = _run_with_pii(tracer=tracer, mode="trace")
    seen = " ".join(message.get("content") or "" for call in provider.calls for message in call)
    assert EMAIL in seen
    assert PHONE in seen


def test_the_run_itself_is_unaffected_by_redaction(tracer) -> None:
    _run_with_pii(tracer=tracer, mode="trace")
    finished = read_trace(tracer.path).first("run_finished")
    assert finished is not None
    assert RunOutput.model_validate(finished.payload).status == "ok"


def test_redaction_cannot_be_enabled_after_the_first_event(tmp_path) -> None:
    """Half a redacted trace is worse than none: the difference is invisible on inspection."""
    writer = TraceWriter(tmp_path, new_trace_id())
    writer.emit("first")
    with pytest.raises(TraceError, match="before the first event"):
        writer.enable_redaction(Redactor.from_names(["email"]))
    writer.close()


def test_a_closed_trace_refuses_redaction(tmp_path) -> None:
    writer = TraceWriter(tmp_path, new_trace_id())
    writer.close()
    with pytest.raises(TraceError, match="closed"):
        writer.enable_redaction(Redactor.from_names(["email"]))


def test_the_writer_reports_whether_it_is_redacting(tmp_path) -> None:
    writer = TraceWriter(tmp_path, new_trace_id())
    assert writer.redacting is False
    writer.enable_redaction(Redactor.from_names(["email"]))
    assert writer.redacting is True
    writer.close()


def test_an_unredacted_writer_emits_no_summary(tmp_path) -> None:
    writer = TraceWriter(tmp_path, new_trace_id())
    writer.emit("only")
    writer.close()
    assert read_trace(writer.path).first("redaction") is None


# ------------------------------------------------------------------ replay refuses


def test_a_redacted_trace_refuses_replay(tmp_path, tracer) -> None:
    _run_with_pii(tracer=tracer, mode="trace")
    with pytest.raises(ReplayUnavailable, match="redaction mode"):
        replay(tracer.path, trace_dir=tmp_path / "replay")


def test_the_refusal_is_specific_but_still_a_divergence(tmp_path, tracer) -> None:
    """Specific, so nobody hunts for a defect that is not there; a divergence, so existing
    handlers keep working."""
    _run_with_pii(tracer=tracer, mode="trace")
    assert issubclass(ReplayUnavailable, ReplayDivergence)
    with pytest.raises(ReplayDivergence):
        replay(tracer.path, trace_dir=tmp_path / "replay")


def test_the_refusal_says_what_to_do(tmp_path, tracer) -> None:
    _run_with_pii(tracer=tracer, mode="trace")
    with pytest.raises(ReplayUnavailable) as caught:
        replay(tracer.path, trace_dir=tmp_path / "replay")
    assert "redaction off" in str(caught.value)


def test_an_unredacted_trace_still_replays(tmp_path, tracer) -> None:
    """The default must not have been broken by adding the option."""
    _run_with_pii(tracer=tracer, mode="off")
    replayed = replay(tracer.path, trace_dir=tmp_path / "replay")
    assert replayed.status == "ok"
