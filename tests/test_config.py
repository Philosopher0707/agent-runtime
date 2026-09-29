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


def test_an_empty_api_key_env_explains_itself() -> None:
    """The field names an environment variable; it does not hold the key.

    Someone setting up a real endpoint emptied it, and the schema error they got was
    `Input should be a valid string`, which does not say what the field is for. A loud
    failure that does not explain itself costs a debugging session.
    """
    with pytest.raises(ConfigError) as caught:
        validate_config({**MINIMAL, "provider": {"kind": "openai_compat", "api_key_env": ""}})
    message = str(caught.value)
    assert "names the environment variable" in message
    assert ".env" in message


def test_a_whitespace_api_key_env_is_also_refused() -> None:
    with pytest.raises(ConfigError, match="names the environment variable"):
        validate_config({**MINIMAL, "provider": {"kind": "openai_compat", "api_key_env": "   "}})


def test_an_api_key_env_is_trimmed() -> None:
    config = validate_config(
        {
            **MINIMAL,
            "provider": {
                "kind": "openai_compat",
                "api_key_env": " AGENT_API_KEY ",
                "price_input_per_mtok": 0.5,
            },
        }
    )
    assert config.provider.api_key_env == "AGENT_API_KEY"


def test_a_schema_outside_the_validator_subset_is_refused() -> None:
    """The same defect as the cost bound above, in a different currency.

    `runtime/structured.py` enforces a documented subset and *ignores* the rest. Ignoring is
    reasonable for a subset validator; ignoring **silently** while the configuration believes the
    answer is checked is not. A schema using `$ref` reads as a contract about the output and is not
    one — so it is refused at load, where the author is looking, rather than discovered by a run
    that accepted an answer the configuration thought it had forbidden.
    """
    with pytest.raises(ConfigError, match=r"does not\s+enforce"):
        validate_config(
            {
                **MINIMAL,
                "output": {"format": "json", "schema": {"$ref": "#/definitions/missing"}},
            }
        )


def test_the_schema_refusal_names_the_way_out() -> None:
    with pytest.raises(ConfigError) as caught:
        validate_config(
            {
                **MINIMAL,
                "output": {"format": "json", "schema": {"type": "object", "allOf": []}},
            }
        )
    message = str(caught.value)
    assert "allOf" in message
    assert "type" in message, "the message must say what *is* enforced, not only what is not"
    assert "decisions/0008" in message


def test_a_schema_inside_the_subset_is_accepted() -> None:
    """The guard on the guard: the refusal must not fire on the schemas the repo actually ships."""
    config = validate_config(
        {
            **MINIMAL,
            "output": {
                "format": "json",
                "schema": {
                    "type": "object",
                    "required": ["findings"],
                    "additionalProperties": False,
                    "properties": {
                        "findings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"verdict": {"enum": ["yes", "no"]}},
                            },
                        }
                    },
                },
            },
        }
    )
    assert config.output.schema_ is not None


def test_property_names_are_not_mistaken_for_keywords() -> None:
    """The walk has to know that keys inside `properties` are *names*. A version that did not
    reported `findings` and `verdict` as unsupported keywords, which would have refused every real
    configuration in this repository."""
    from runtime.structured import unsupported_keywords

    schema = {
        "type": "object",
        "properties": {"allOf": {"type": "string"}, "$ref": {"type": "number"}},
    }
    assert unsupported_keywords(schema) == [], "a property *named* allOf is not the allOf keyword"


def test_a_cost_budget_that_cannot_trip_is_refused() -> None:
    """Cost is computed from `price_*`, which default to 0.0.

    `max_cost_usd` is `gt=0`, so a configuration always *claims* a positive cost bound and
    there is no way to opt out. An `openai_compat` configuration that omits prices
    therefore reports $0.000000 for every run while spending real money, and the bound it
    declares can never fire — silently.
    """
    with pytest.raises(ConfigError, match="can never trip"):
        validate_config(
            {**MINIMAL, "provider": {"kind": "openai_compat", "api_key_env": "AGENT_API_KEY"}}
        )


def test_the_cost_refusal_names_the_way_out() -> None:
    with pytest.raises(ConfigError) as caught:
        validate_config(
            {**MINIMAL, "provider": {"kind": "openai_compat", "api_key_env": "AGENT_API_KEY"}}
        )
    message = str(caught.value)
    assert "price_input_per_mtok" in message
    assert "price_output_per_mtok" in message


def test_a_stub_needs_no_prices() -> None:
    """A stub has no spend, so a zero price is correct and the worked examples stay valid."""
    config = validate_config({**MINIMAL, "provider": {"kind": "stub"}})
    assert config.budget.max_cost_usd > 0
    assert config.provider.price_input_per_mtok == 0.0


def test_one_price_is_enough_to_make_the_bound_real() -> None:
    config = validate_config(
        {
            **MINIMAL,
            "provider": {
                "kind": "openai_compat",
                "api_key_env": "AGENT_API_KEY",
                "price_output_per_mtok": 1.5,
            },
        }
    )
    assert config.provider.price_output_per_mtok == 1.5


def test_the_shipped_openai_compat_example_passes() -> None:
    """The validator must not make the worked example unloadable."""
    config = load_config(CONFIGS / "openai_compat.yaml")
    assert config.provider.price_input_per_mtok > 0


def test_the_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config_by_name("openai_compat", root=CONFIGS)
    monkeypatch.setenv("AGENT_API_KEY", "from-env")
    assert read_api_key(config.provider) == "from-env"
    monkeypatch.delenv("AGENT_API_KEY")
    assert read_api_key(config.provider) is None


def test_openai_compat_without_a_base_url_is_refused() -> None:
    config = validate_config(
        {
            **MINIMAL,
            "provider": {
                "kind": "openai_compat",
                "base_url": None,
                "price_input_per_mtok": 0.5,
            },
        }
    )
    with pytest.raises(ConfigError, match="base_url"):
        build_provider(config.provider)


def test_the_stub_provider_honours_the_configured_prices() -> None:
    config = validate_config({**MINIMAL, "provider": {"kind": "stub", "price_input_per_mtok": 5.0}})
    provider = build_provider(config.provider)
    assert provider._price_in == 5.0


# --------------------------------------- a configuration declares its tools' arguments


def test_tool_options_reach_the_tool(tmp_path) -> None:
    """A capability configures its own tools; the core names none of them.

    Before decisions/0027 the composition root passed `{"write_note": {"root": ...}}` itself, so
    only a tool that happened to be called `write_note` could ever receive an argument — and the
    triage tools worked only because their default directory happened to be right.
    """
    import json

    from tools.triage import ListMessagesArgs, ListMessagesTool

    (tmp_path / "042.json").write_text(json.dumps({"subject": "declared"}), encoding="utf-8")
    config = validate_config(
        {
            **MINIMAL,
            "tools": ["list_messages"],
            "tool_options": {"list_messages": {"root": str(tmp_path)}},
        }
    )
    registry = build_tools(config)
    try:
        tool = registry.get("list_messages")
        assert isinstance(tool, ListMessagesTool)
        assert "042" in tool.invoke(ListMessagesArgs()), "the configuration's option was ignored"
    finally:
        registry.close()


def test_a_caller_may_override_what_the_configuration_declares(tmp_path) -> None:
    """A CLI flag or a per-case temporary directory still works — it just does not live in the
    core. The override wins over the declared value."""
    import json

    from tools.triage import ListMessagesArgs, ListMessagesTool

    declared, redirected = tmp_path / "declared", tmp_path / "redirected"
    for directory, message_id in ((declared, "001"), (redirected, "999")):
        directory.mkdir()
        (directory / f"{message_id}.json").write_text(
            json.dumps({"subject": message_id}), encoding="utf-8"
        )

    config = validate_config(
        {
            **MINIMAL,
            "tools": ["list_messages"],
            "tool_options": {"list_messages": {"root": str(declared)}},
        }
    )
    registry = build_tools(config, overrides={"list_messages": {"root": str(redirected)}})
    try:
        tool = registry.get("list_messages")
        assert isinstance(tool, ListMessagesTool)
        listed = tool.invoke(ListMessagesArgs())
        assert "999" in listed, "the override did not win"
        assert "001" not in listed
    finally:
        registry.close()
