"""Configuration: the only place a capability is declared.

A configuration is a named bundle of a system prompt, a tool set, a model, and an
eval suite. Adding a capability means adding a file under ``configs/`` — never a
branch in the core.

The budget block is required, not defaulted. A configuration that cannot state its
own bounds is not a configuration.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, Field, field_validator, model_validator

from runtime.redact import DEFAULT_PATTERNS, PATTERN_NAMES
from runtime.schemas import Contract

#: Repository-root-relative default for the worked-example configs.
DEFAULT_CONFIG_DIR = Path("configs")


class ConfigError(Exception):
    """A configuration could not be loaded or is not a configuration."""


class ProviderConfig(Contract):
    """Which model, reached how. Secrets are named, never stored."""

    kind: Literal["stub", "openai_compat"] = "stub"
    model: str = "stub-mid-tier"
    base_url: str | None = None
    #: Name of the environment variable holding the key. The key itself never
    #: enters a config, a prompt, a log line, or a trace.
    api_key_env: str = "AGENT_API_KEY"

    @field_validator("api_key_env")
    @classmethod
    def _must_name_a_variable(cls, value: str) -> str:
        """Refuse an empty name with an explanation, because the mistake is an easy one.

        The field reads like "the API key (env)" and it is not — it names the environment
        variable that holds the key. Someone emptied it while setting up a real endpoint,
        and the schema error they got was `Input should be a valid string`, which does not
        say what the field is for. Failing loudly is right; failing *informatively* is
        better, and this is the one place that can say it.
        """
        if not value.strip():
            raise ValueError(
                "api_key_env names the environment variable that holds the key "
                "(for example AGENT_API_KEY). It does not hold the key itself. Put the key "
                "in .env, or export it — never in a configuration file, which is committed."
            )
        return value.strip()

    price_input_per_mtok: float = Field(default=0.0, ge=0.0)
    price_output_per_mtok: float = Field(default=0.0, ge=0.0)
    timeout_s: float = Field(default=60.0, gt=0.0)
    #: A scripted model, so a worked example can be run with no API key and no
    #: network. Each entry is a ``ModelResponse`` payload, in order.
    stub_script: list[dict[str, Any]] = Field(default_factory=list)
    stub_final: str = "Done."


class BudgetConfig(Contract):
    """All four bounds, all required, none optional.

    ``ge=0`` rather than ``gt=0`` on the countable bounds: zero is a legitimate
    way to say "start nothing", and a zero bound must terminate rather than hang.
    """

    max_steps: int = Field(ge=0)
    max_tokens_total: int = Field(ge=0)
    max_wall_clock_s: float = Field(gt=0.0)
    max_cost_usd: float = Field(gt=0.0)


class ContextConfig(Contract):
    max_prompt_tokens: int = Field(default=8000, gt=0)
    #: Token estimation is a heuristic by design — see docs/decisions/0005.
    #:
    #: Calibrated against a real endpoint: English prose tokenises at roughly 4.6
    #: characters per token, so 4.0 is slightly conservative, which is the safe
    #: direction. Under-estimating means the runtime thinks it has room it does not.
    chars_per_token: float = Field(default=4.0, gt=0.0)
    #: The same ratio for the tool schemas, which are JSON.
    #:
    #: JSON tokenises far worse than prose — lots of punctuation, short repeated keys.
    #: Measured at **2.0 characters per token** against OpenRouter: 1220 characters of
    #: schema cost roughly 610 prompt tokens. Using the prose ratio here under-counted
    #: the fixed per-request overhead by half, on top of the larger defect that the
    #: schemas were not counted at all.
    schema_chars_per_token: float = Field(default=2.0, gt=0.0)
    summarise_above_tokens: int = Field(default=600, ge=0)
    summary_chars: int = Field(default=200, gt=0)
    #: The most characters of rendered tool calls one assistant turn may contribute.
    #:
    #: A tool *result* is bounded by ``guardrails.untrusted_max_chars``. Nothing bounded what
    #: the model *asked for*: the assistant turn carries every call's arguments in full, so
    #: sixteen calls with 8,000-character arguments added ~65,000 tokens in a single step —
    #: which summarisation cannot touch, because it only rewrites tool results, and which the
    #: hard ceiling could only answer by dropping the whole request. See
    #: docs/decisions/0025.
    #:
    #: The bound applies to the turn's calls **together**, not to each call, because the thing
    #: that must not happen is one turn dominating the prompt — and sixteen bounded calls still
    #: add up.
    max_call_chars: int = Field(default=4_000, gt=0)


class OutputConfig(Contract):
    format: Literal["text", "json"] = "text"
    #: Optional JSON Schema subset the final answer must satisfy.
    #:
    #: Accepted under both ``schema`` (how a YAML file writes it) and ``schema_`` (how
    #: ``model_dump`` emits it), so a configuration survives the dump/validate round trip
    #: that trace replay depends on.
    schema_: dict[str, Any] | None = Field(
        default=None,
        alias="schema",
        validation_alias=AliasChoices("schema", "schema_"),
    )
    max_repair_attempts: int = Field(default=1, ge=0)


class RedactionConfig(Contract):
    """What must be redacted before logging. See ``runtime/redact.py``.

    ``off`` is the default, and that is a deliberate trade rather than an omission: a
    redacted trace cannot be replayed, because replay rebuilds each prompt from the trace
    and compares its hash against the recorded one. Turning redaction on buys a safer
    durable record at the cost of the ability to reproduce the run.
    """

    mode: Literal["off", "trace"] = "off"
    patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_PATTERNS))

    @field_validator("patterns")
    @classmethod
    def _only_known_patterns(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - PATTERN_NAMES)
        if unknown:
            raise ValueError(
                f"unknown redaction pattern(s): {unknown}. Known: {sorted(PATTERN_NAMES)}"
            )
        return list(dict.fromkeys(value))


class GuardrailConfig(Contract):
    max_input_chars: int = Field(default=20_000, gt=0)
    #: How much of a tool result survives into the prompt.
    untrusted_max_chars: int = Field(default=4_000, gt=0)
    #: How much leading system-prompt text counts as a disclosure if echoed back.
    disclosure_prefix_chars: int = Field(default=120, gt=0)
    #: What is redacted before it reaches the trace. Never the live prompt.
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)


class Configuration(Contract):
    """A named, complete capability declaration."""

    name: str
    description: str = ""
    system_prompt: str
    tools: list[str] = Field(default_factory=list)
    #: Constructor arguments for the tools this configuration names, keyed by tool name.
    #:
    #: Here rather than in the core, because *which* tool needs *what* is a property of the
    #: capability. The composition root used to pass ``{"write_note": {"root": ...}}`` itself,
    #: which meant only a tool that happened to be called `write_note` could ever receive an
    #: argument — and the triage tools worked only because their default directory happened to
    #: be right. A caller may still override one (a CLI flag, a per-case temporary directory);
    #: what it may not do is make the *core* know a tool's name.
    tool_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    #: No default. Every configuration states its bounds.
    budget: BudgetConfig
    context: ContextConfig = Field(default_factory=ContextConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    guardrails: GuardrailConfig = Field(default_factory=GuardrailConfig)

    @model_validator(mode="after")
    def _a_cost_budget_must_be_able_to_bind(self) -> Configuration:
        """Refuse a configuration whose cost bound can never trip.

        ``max_cost_usd`` is ``gt=0``, so a configuration always claims a positive cost
        bound — and there is no way to opt out by setting it to zero. Cost is computed
        from ``price_*``, which default to ``0.0``. So an ``openai_compat`` configuration
        that omits prices reports ``$0.000000`` for every run while spending real money,
        and the bound it declares can never fire. Silently.

        Scoped to providers that actually cost money: a stub has no spend, so a zero
        price is correct there and the three worked examples stay valid.
        """
        if self.provider.kind != "openai_compat":
            return self
        if self.provider.price_input_per_mtok or self.provider.price_output_per_mtok:
            return self
        raise ValueError(
            f"budget.max_cost_usd is {self.budget.max_cost_usd} but both "
            f"provider prices are 0.0, so every run reports $0.00 and this bound can never "
            f"trip. Set provider.price_input_per_mtok and provider.price_output_per_mtok to "
            f"the endpoint's real rates — the bound is only as real as those two numbers."
        )


def config_path(name: str, root: str | Path = DEFAULT_CONFIG_DIR) -> Path:
    """Resolve a configuration name to a file path."""
    root = Path(root)
    candidate = root / name
    if candidate.suffix in {".yaml", ".yml"}:
        return candidate
    return root / f"{name}.yaml"


def load_config(path: str | Path) -> Configuration:
    """Load and validate one configuration file."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"no such configuration file: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - malformed YAML is user error
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return validate_config(raw, origin=str(path))


def validate_config(raw: dict[str, Any], *, origin: str = "<dict>") -> Configuration:
    """Validate a raw mapping into a Configuration, naming the origin on failure."""
    from pydantic import ValidationError

    try:
        return Configuration.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{origin}: {exc}") from exc


def load_config_by_name(name: str, *, root: str | Path = DEFAULT_CONFIG_DIR) -> Configuration:
    return load_config(config_path(name, root))


def available_configs(root: str | Path = DEFAULT_CONFIG_DIR) -> list[str]:
    """Names of the worked examples, sorted. The fastest way to see the design."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.yaml"))


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def apply_overrides(config: Configuration, overrides: dict[str, Any]) -> Configuration:
    """Return a new configuration with ``overrides`` deep-merged in.

    Used by the eval harness to narrow a budget or force an output format without
    duplicating a whole config file. Overrides use the same keys a config file uses —
    the base is dumped by alias so the two vocabularies cannot collide. Overrides go
    through the same validation as a file on disk; there is no privileged path around
    the schema.
    """
    if not overrides:
        return config
    merged = _deep_merge(config.model_dump(by_alias=True), overrides)
    return validate_config(merged, origin=f"<overrides for {config.name}>")


def read_api_key(provider: ProviderConfig) -> str | None:
    """Read the provider key from the environment. Never from a file in the repo."""
    return os.environ.get(provider.api_key_env) or None


#: The file a local run reads environment variables from, if it exists.
ENV_FILE = Path(".env")


def parse_env_file(text: str) -> dict[str, str]:
    """Parse the ``.env`` shapes people actually write.

    Deliberately small: ``KEY=value``, ``export KEY=value``, quoted values, comments on
    their own line, and a trailing ``#`` comment after an unquoted value. No
    interpolation, no multiline values, no shell evaluation — a config file that can run
    code is a different kind of thing from a config file.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()

        values[key] = value
    return values


def load_env_file(path: str | Path | None = None) -> int:
    """Load ``.env`` into the environment, and return how many variables were set.

    **A real environment variable always wins.** That is the twelve-factor precedence, and
    it is what makes a shell export usable as a temporary override of the file.

    This exists because it was missing. ``.env.example`` and a ``.gitignore`` entry for
    ``.env`` both imply the convention, and nothing implemented it — so a key placed in
    ``.env`` was silently ignored, the provider was called unauthenticated, and the only
    symptom was a 401 from the vendor with nothing pointing at the cause.
    """
    path = Path(path) if path is not None else ENV_FILE
    if not path.is_file():
        return 0

    applied = 0
    for key, value in parse_env_file(path.read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value
            applied += 1
    return applied


def iter_tool_names(configs: Iterable[Configuration]) -> set[str]:
    """Every tool named by any configuration. Useful for catalogue coverage checks."""
    return {name for config in configs for name in config.tools}


__all__ = [
    "DEFAULT_CONFIG_DIR",
    "ENV_FILE",
    "ConfigError",
    "Configuration",
    "ContextConfig",
    "GuardrailConfig",
    "OutputConfig",
    "ProviderConfig",
    "apply_overrides",
    "available_configs",
    "config_path",
    "load_config",
    "load_config_by_name",
    "load_env_file",
    "parse_env_file",
    "read_api_key",
    "validate_config",
]
