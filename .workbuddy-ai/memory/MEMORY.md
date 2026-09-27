# MEMORY.md — agent-runtime (this project)

Durable notes for working in this repository. Read `AGENTS.md` first; it is the spec and
it wins over anything here.

## The one rule

`runtime/loop.py` contains **no domain logic and no provider-specific code**. It knows the
`Provider` protocol, the `ToolBoundary` protocol, the budget, and the failure taxonomy —
plus exactly one tool *name* (`ask_clarification`, a control tool it intercepts). If you
are writing an `if` about a task, tool, or vendor inside the loop, it belongs in a
configuration, a tool, or a model adapter.

Checkable: every vendor string lives in `providers/`; every concrete implementation is
named in `runtime/factory.py` and nowhere else.

## Conventions that are not obvious from the code

- **`make ci` is the single definition of the gates.** `.github/workflows/ci.yml` installs
  with `uv sync --frozen` and then runs `make ci` and nothing else. Never invoke a gate from
  the workflow — `tests/test_ci_contract.py` fails if you do, and if a gate is dropped from
  the `ci` recipe. `make ci` runs `lock-check check eval markers smoke`, in order, stopping
  at the first failure. `--frozen` matters: without it a stale `uv.lock` is silently
  re-resolved rather than reported.
- **Do not pin `UV_PYTHON` in the Makefile.** `uv` reads `.python-version`. A default of
  `command -v python3` looks harmless and breaks CI, where the runner's `python3` may be
  older than the project requires.
- **Redaction and replay are mutually exclusive — do not "fix" one by weakening the
  other.** The prompt is never redacted (it would silently change the task); the trace is,
  at the single `TraceWriter.emit` chokepoint. A redacted trace refuses replay with
  `ReplayUnavailable`. `guardrails.redaction.mode` is `off` by default precisely because
  turning it on costs replayability. Decision: `docs/decisions/0012`.
- **The injection markers are measured, not asserted.** `make markers` grades the rule
  against `evals/markers.py` and exits non-zero below threshold; the same thresholds are
  enforced in `tests/test_marker_precision.py`, so the report and the tripwire cannot
  disagree. **Every marker must be load-bearing** — an ablation test fails if removing one
  costs no recall. Adding a marker requires evidence it earns its place; a marker that
  catches nothing is pure false-positive risk. The false-positive budget is **zero**,
  because every benign sample was chosen by hand to represent real tool output. The
  tempting wrong fix is to delete the benign sample that fails.
- **`AGENTS_LEARNING.md` is maintained as we go.** It is the project's learning log —
  surprises, mistakes, open questions — and it is the *first* entry under "Where to look"
  in `AGENTS.md`. Append a dated entry (`### YYYY-MM-DD — title`) whenever you learn
  something that would change your next move. Never rewrite an entry; append one that
  supersedes it and says so. `tests/test_repo_hygiene.py` enforces that the log exists, is
  discoverable from `AGENTS.md` and `README.md`, and that every entry is dated.
- **`AGENTS.md` has an 8,000-byte budget** and sits close to it. The loader cuts the tail,
  so exceeding it silently truncates instructions. The same test file fails loudly if the
  budget is exceeded — move detail into `docs/` rather than growing the spec.
- **Adding a capability is a config file, never a branch.** New tool → `tools/` + a name
  in `tools/builtin.py`. New capability → `configs/<name>.yaml`. All four budget bounds
  are mandatory; a config missing one fails to load.
- **`tests/helpers.py` is not collected** (no `test_` prefix). It holds `execute()`,
  `make_config()`, and the scripted-turn builders (`text`, `tool_call`, `refusal`).
- **A new failure class needs a test that forces it.** The taxonomy is asserted to be
  fully covered by `tests/test_taxonomy.py::test_every_failure_class_has_a_test` — adding
  a class without a test fails the suite.
- **`evals/` is the only authority on whether a change helped.** `make eval` gates at
  threshold 1.0 because every case is deterministic. Record the score in the commit
  message when it moves.
- **Docs are append-only where the spec says so:** `docs/decisions/` — one file per
  decision, numbered, never rewritten.
- **Never quote a test or eval count from memory.** Run it.

## Traps already paid for

- `Budget.check(include_steps=False)` when charging usage. The step bound gates *starting*
  a step; applying it after a step has run aborts a run that was already affordable.
- pydantic: `validation_alias` alone does not drive serialization. A field that must
  survive `model_dump` → `model_validate` (replay depends on this) needs `alias=` too.
  Dump configurations **by alias** so overrides and dumps share one vocabulary.
- pytest writes its scratch space outside the repo by default, which some sandboxes deny.
  `--basetemp=.pytest-tmp` is set in `pyproject.toml`; do not remove it.
- Replay needs the tool **descriptors** from the trace, not rebuilt from the catalogue.
  They are inside every prompt hash.
- The loop finishes exactly once per run: use `_note_stop` (records, returns the reason)
  from the paths that hand a reason back, and let the loop call `_finish`. Calling
  `_stop(...).reason` double-emits `run_finished`.
- **A threshold is only a tripwire if the corpus makes it sharp.** A 5% false-positive
  budget on a 26-sample corpus tolerates exactly one failure, so it never bit — defeating
  the proximity rule entirely passed the suite. Corollary: a rule needs a *corpus sample*
  covering it, not just a unit test, or the metric will not notice its removal.

## Environment

Managed Python 3.13.12 and `uv` live outside the repo
(`~/.workbuddy-ai/binaries/python/envs/default/bin/uv`). `uv sync --all-extras` creates
`.venv` inside the project. `make ci` is the single gate (`lock-check check eval markers
smoke`), and CI runs it on every push.

Remote: `https://github.com/Philosopher0707/agent-runtime` (private). `gh` is not installed
on this machine, so use the API directly for repo operations; the OAuth token is in the OS
keychain, and `git push` works through the configured `osxkeychain` helper. Never write a
token into `.git/config` or a remote URL.
