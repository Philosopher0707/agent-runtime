"""Configuration: the shape a capability must have, and the worked examples.

The budget block being *required* is the load-bearing assertion here. A configuration
that cannot state its own bounds is not a configuration, and the schema is where that
is enforced — not a convention someone remembers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import (
    ConfigError,
    apply_overrides,
    available_configs,
    load_config,
    load_config_by_name,
    read_api_key,
    validate_config,
)
from runtime.factory import build_provider, build_tools
from tools.catalogue import available_tool_names

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = REPO_ROOT / "configs"

MINIMAL: dict[str, object] = {
    "name": "minimal",
    "system_prompt": "You are a test assistant.",
    "tools": ["echo"],
    "provider": {"kind": "stub"},
    "budget": {
        "max_steps": 4,
        "max_tokens_total": 1000,
        "max_wall_clock_s": 10,
        "max_cost_usd": 0.1,
    },
}


def test_a_minimal_configuration_validates() -> None:
    assert validate_config(MINIMAL).name == "minimal"


def test_the_budget_block_is_required() -> None:
    without = {key: value for key, value in MINIMAL.items() if key != "budget"}
    with pytest.raises(ConfigError, match="budget"):
        validate_config(without)


@pytest.mark.parametrize(
    "missing",
    ["max_steps", "max_tokens_total", "max_wall_clock_s", "max_cost_usd"],
)
def test_each_budget_bound_is_required(missing: str) -> None:
    """None of the four is optional. Dropping any one of them is a configuration error."""
    budget = {
        key: value
        for key, value in MINIMAL["budget"].items()  # type: ignore[union-attr]
        if key != missing
    }
    with pytest.raises(ConfigError, match=missing):
        validate_config({**MINIMAL, "budget": budget})


def test_zero_is_allowed_for_countable_bounds_but_not_for_time_or_cost() -> None:
    ok = {**MINIMAL, "budget": {**MINIMAL["budget"], "max_steps": 0, "max_tokens_total": 0}}  # type: ignore[dict-item]
    assert validate_config(ok).budget.max_steps == 0

    with pytest.raises(ConfigError, match="max_wall_clock_s"):
        validate_config({**MINIMAL, "budget": {**MINIMAL["budget"], "max_wall_clock_s": 0}})  # type: ignore[dict-item]


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ConfigError, match="extra"):
        validate_config({**MINIMAL, "temperature": 0.7})


def test_a_configuration_round_trips_through_a_dump() -> None:
    """Replay depends on this: the trace stores a dump and validates it back."""
    from runtime.config import Configuration

    config = load_config(CONFIGS / "structured_report.yaml")
    assert Configuration.model_validate(config.model_dump()).output.schema_ is not None
    assert Configuration.model_validate(config.model_dump(by_alias=True)).output.schema_ is not None


def test_a_missing_file_is_reported() -> None:
    with pytest.raises(ConfigError, match="no such configuration file"):
        load_config(CONFIGS / "absent.yaml")


def test_malformed_yaml_is_reported(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(broken)


def test_a_non_mapping_document_is_reported(tmp_path: Path) -> None:
    flat = tmp_path / "flat.yaml"
    flat.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_config(flat)


def test_overrides_are_deep_merged_and_validated() -> None:
    config = load_config_by_name("default", root=CONFIGS)
    narrowed = apply_overrides(config, {"budget": {"max_steps": 2}})
    assert narrowed.budget.max_steps == 2
    assert narrowed.budget.max_tokens_total == config.budget.max_tokens_total
    assert narrowed.tools == config.tools


def test_an_invalid_override_is_refused() -> None:
    config = load_config_by_name("default", root=CONFIGS)
    with pytest.raises(ConfigError, match="max_steps"):
        apply_overrides(config, {"budget": {"max_steps": -1}})


def test_no_overrides_returns_the_same_configuration() -> None:
    config = load_config_by_name("default", root=CONFIGS)
    assert apply_overrides(config, {}) is config


def test_available_configs_lists_the_worked_examples() -> None:
    names = available_configs(CONFIGS)
    assert {"default", "structured_report", "stateful_notes", "openai_compat"} <= set(names)


def test_every_worked_example_loads_and_is_constructible() -> None:
    """The examples are the fastest way to understand the design, so they must work."""
    names = available_configs(CONFIGS)
    assert names, "no worked examples found"
    for name in names:
        config = load_config_by_name(name, root=CONFIGS)
        build_provider(config.provider)  # constructs; does not call out
        registry = build_tools(config)
        try:
            for tool in config.tools:
                assert tool in registry.names(), f"{name} names an unregistered tool {tool!r}"
        finally:
            registry.close()


def test_every_named_tool_exists_in_the_catalogue() -> None:
    known = set(available_tool_names())
    for name in available_configs(CONFIGS):
        config = load_config_by_name(name, root=CONFIGS)
        unknown = [tool for tool in config.tools if tool not in known]
        assert not unknown, f"{name} names unknown tools: {unknown}"


def test_a_configuration_naming_an_unknown_tool_fails_loudly() -> None:
    from tools.catalogue import UnknownToolError

    config = validate_config({**MINIMAL, "tools": ["echo", "nonexistent"]})
    with pytest.raises(UnknownToolError, match="nonexistent"):
        build_tools(config)


def test_a_secret_is_named_not_stored() -> None:
    config = load_config_by_name("openai_compat", root=CONFIGS)
    assert config.provider.api_key_env == "AGENT_API_KEY"
    # The key itself is nowhere in the configuration.
    dumped = config.model_dump(mode="json")
    assert "api_key" not in dumped["provider"]


def test_the_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config_by_name("openai_compat", root=CONFIGS)
    monkeypatch.setenv("AGENT_API_KEY", "from-env")
    assert read_api_key(config.provider) == "from-env"
    monkeypatch.delenv("AGENT_API_KEY")
    assert read_api_key(config.provider) is None


def test_openai_compat_without_a_base_url_is_refused() -> None:
    config = validate_config({**MINIMAL, "provider": {"kind": "openai_compat", "base_url": None}})
    with pytest.raises(ConfigError, match="base_url"):
        build_provider(config.provider)


def test_the_stub_provider_honours_the_configured_prices() -> None:
    config = validate_config({**MINIMAL, "provider": {"kind": "stub", "price_input_per_mtok": 5.0}})
    provider = build_provider(config.provider)
    assert provider._price_in == 5.0
