# 0027 — A configuration declares its tools' constructor arguments

Date: 2026-09-28
Status: accepted

## Context

Found by asking whether the architecture is additive. It is — adding the first real domain
touched eighteen new files, one line in an existing source file, and no core logic. But the
measurement exposed the one place the claim leaked:

```python
# runtime/factory.py
tool_kwargs = {"write_note": {"root": notes_root}}
```

**The composition root named a tool.** The consequences were not theoretical:

- **Only a tool that happened to be called `write_note` could receive a constructor argument.**
- **The triage tools worked because their default (`Path("messages")`) was right**, not because a
  configuration chose it. A capability could not configure its own tools at all.
- The same hard-coded name appeared in **three** places — `runtime/factory.py`,
  `tests/helpers.py`, and `evals/runner.py` — so the harness and production could drift, which is
  the shape of the bug in the first place.

## Decision

**A configuration declares its tools' constructor arguments, in `tool_options`.** A caller may
override one; what it may not do is make the *core* know a tool's name.

```yaml
tools:
  - write_note

tool_options:
  write_note:
    root: .notes
```

Four parts:

- **`Configuration.tool_options`** — `dict[str, dict[str, Any]]`, keyed by tool name.
- **`build_tools(config, overrides=...)`** reads it and merges the caller's overrides on top.
  `runtime/factory.py` names no tool.
- **The entry points still know their own concepts.** `--notes-root` is a flag *about notes*, so
  `cli.py` mapping it to `write_note` is the flag's meaning, not a leak. The same for
  `service/app.py` and the eval harnesses. **The line is the core, not the composition.**
- **`tests/helpers.py` and `evals/runner.py` now build through `build_tools`** rather than
  assembling `tool_kwargs` themselves, so the harness exercises the same wiring a run does.

## Consequences

- **The check can cover the whole core.** It was scoped to `runtime/loop.py` when written,
  because `factory.py` legitimately named `write_note`; it now covers every file in `runtime/`,
  which is what the rule actually says.
- **`configs/triage.yaml` declares its message root** — three entries it previously got only by
  default. That is the visible difference between a capability that *happens* to work and one
  that says what it means.
- Two tests in `tests/test_config.py`: the configuration's options reach the tool, and a caller's
  override wins.

## The check had the bug it was written to catch

The first version searched the core's **text**, and it flagged `config.py` and `factory.py` for
naming `write_note` — **in the docstrings explaining why they no longer name it in code.**

That is the first rule of the tripwire catalogue: *AST over text, always.* The check now walks the
AST and excludes docstrings (and comments, which are not in the AST at all). A test asserts the
reader ignores prose, because the mistake is easy to make twice.

## Alternatives considered

**Pass the options through a generic `**kwargs` to every tool.** Rejected: it would make a tool's
constructor arguments depend on the caller's guess, and a tool that does not accept them would
fail at build time with a message about the wrong thing.

**Have each tool declare which option names it wants.** Rejected as more machinery than the
problem needs. `tool_options` is a map a configuration can read and a person can audit.

**Leave it — nothing was broken.** Rejected: the roadmap's own standard is that a declared
capability must be able to declare things, and "a capability cannot configure its tools" is a
limitation with no owner. It was also, measurably, the one place the additive claim did not hold.
