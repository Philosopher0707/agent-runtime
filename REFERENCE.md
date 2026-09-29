# REFERENCE.md — working in this repository

The durable knowledge for working here: the conventions the code does not show you, and the mistakes
that have already been paid for. Read it before your first change.

**`AGENTS.md` is the spec and wins over this file.** The reasoning behind a rule lives in
[`docs/decisions/`](docs/decisions/0001-run-protocol.md); the stories behind it live in
[`AGENTS_LEARNING.md`](AGENTS_LEARNING.md). This is the short form — what to do, and what has already
cost someone time.

## The one rule

`runtime/loop.py` holds **no domain logic and no provider-specific code**: it knows the `Provider`
protocol, the `ToolBoundary` protocol, the log boundary, the budget and the failure taxonomy, plus one
tool *name* — `ask_clarification`, a control tool it intercepts. An `if` about a task, tool or vendor
belongs in a configuration, a tool, or a model adapter. Checkable: vendor strings live in `providers/`,
and concrete implementations are named in `runtime/factory.py` and nowhere else.

## Conventions the code does not show you

- **A capability is a config file, never a branch.** New tool → `tools/` plus a name in
  `tools/builtin.py`; new capability → `configs/<name>.yaml`. All four budget bounds are mandatory.
- **The loop absorbs a collaborator that breaks its contract, at the seam — and nothing else.**
  `run()` returns a `RunOutput` for anything that goes wrong in the task. The provider and tool seams
  absorb an *unexpected* exception and classify it with the existing taxonomy, naming the type in the
  detail. What still escapes: a `RunLog` that cannot write, a process stopping
  (`KeyboardInterrupt`/`SystemExit`), and anything deriving from **`ProviderSignal`** — a provider
  raising that is *telling* you something (a replay diverged), not failing, and filing it as
  `provider_error` would be a plausible-looking lie. The loop's own bugs stay loud on purpose. A
  catch-all at a seam destroys invariants defended elsewhere: this one swallowed `ReplayDivergence`
  until two replay tests caught it. Decision
  [0034](docs/decisions/0034-the-orchestrators-boundary.md).
- **The tool seam absorbs what it owns, and only that.** `ToolRegistry.dispatch` never raises for a
  tool-level problem: a tool's args model raising something pydantic did not wrap, and the executor
  refusing the work, both become records. A broken **injected callable** (the monotonic clock, the
  backoff `sleep`) is a *composition* error and still raises — the loop absorbs it, the registry does
  not. `register()` also proves `describe()` works, because the descriptor is in every prompt and a
  tool that cannot render one dies at setup. Decision
  [0035](docs/decisions/0035-the-seam-absorbs-what-it-owns.md).
- **A declared output schema must be enforceable.** `runtime/structured.py` implements a six-keyword
  subset and ignores the rest; `Configuration` refuses a schema using anything else at load, so a
  `$ref` cannot read as a contract that nothing enforces. Same argument as
  [0015](docs/decisions/0015-cost-budget-must-bind.md). **Property names are not keywords** — the keys
  inside `properties` are names — and `additionalProperties` may be a boolean. Decision
  [0036](docs/decisions/0036-a-declared-schema-must-be-enforceable.md).
- **A new `.md` needs a row in `scripts/derive_notes.py`, then `make notes`.** `NOTES.md` lists every
  markdown file in the repo with **the claim it leads with, quoted**: a decision quotes its own
  `# NNNN — claim` heading, a guide's sentence is hand-chosen and checked verbatim. The generator
  *refuses* rather than emitting — a file with no row, a row with no file, a quote that is not in the
  file, or a `|` in a cell. `tests/test_notes.py` guards it from two directions, like the register.
  A page of paraphrases would be forty second copies of the truth; this one cannot drift because it
  does not paraphrase.
- **A new `tests/test_*.py` needs a category in `tests/register.py`, then `make register`.**
  `REGISTER.md` is a projection of the suite — numbered `C.N`, **Boundary first**, counts from a real
  `pytest --collect-only`, descriptions cited from each file's docstring — and `tests/test_register.py`
  fails on an uncategorised file, a stale count, or a page that does not match a fresh render. So it is
  a two-file change every time, and the guard runs inside `make check` rather than as a sixth gate.
  **The page records no revision**: a SHA in a file dangles once the branch is rebase-merged; the date
  says *when*, `git log REGISTER.md` says *which*. `test_the_page_records_no_revision` enforces it.
  Decision [0032](docs/decisions/0032-the-register-is-a-projection.md) and its addendum.
- **`runtime/loop.py` must not import `runtime.trace`.** The log is a boundary (`RunLog`, beside
  `ToolBoundary`) that `TraceWriter` satisfies structurally, and two AST tripwires enforce it. The one
  place the loop *raises* for something that is not the task's fault is a log that cannot write — the
  safe outcome is no run, not a run with a silent gap. Decision
  [0033](docs/decisions/0033-the-log-is-a-boundary.md).
- **A tool reports what it consumed; the loop charges the *record*.** `ToolResult(text, spend)` on
  return, `ToolError(spend=...)` when it failed after spending — because the charge runs where the loop
  interprets the record, the only path that also runs on replay. Charging it where the spend is
  *created* made a replayed delegated run report `$0.0073` against a recorded `$0.0174`.
  `Budget.charge` takes numbers, not a child `Budget`. Decision
  [0031](docs/decisions/0031-the-spend-is-in-the-record.md).
- **Every trace line carries `schema_version`** (`TRACE_SCHEMA_VERSION` in `runtime/schemas.py`), and
  `read_trace` refuses an unknown one by name. Bump it when the *meaning* of an existing payload
  changes, not when an optional event is added — an old trace is a fact about the file, not a defect in
  the code (a format change once surfaced as `ReplayDivergence`, the wrong diagnosis). Decision
  [0014](docs/decisions/0014-trace-schema-version.md).
- **`openai_compat` requires at least one non-zero price**, or `max_cost_usd` — `gt=0`, and not
  optional — is a bound that can never fire while reporting `$0.00`. Refused at load. Decision
  [0015](docs/decisions/0015-cost-budget-must-bind.md).
- **Redaction and replay are mutually exclusive.** The prompt is never redacted (it would change the
  task); the trace is, at the single `TraceWriter.emit` chokepoint, and a redacted trace refuses replay
  with `ReplayUnavailable`. `guardrails.redaction.mode` is `off` by default because turning it on costs
  replayability. Decision [0012](docs/decisions/0012-pii-context-and-redaction.md).
- **`.env` is loaded by every entry point; a real environment variable wins.** The key goes in `.env`
  as `AGENT_API_KEY`; endpoint, model and prices go in `configs/openai_compat.yaml`. Guarded by
  `tests/test_env_file.py`: every `__main__` file **outside `tests/`** must call `load_env_file()`, and
  `.env.example` may not advertise a variable nothing reads. (That scope is a correction, not a
  relaxation — the search had been matching its own literal.)
- **The markers are measured, not asserted.** `make markers` grades the rule against `evals/markers.py`
  and exits non-zero below threshold; `tests/test_marker_precision.py` enforces the same thresholds, so
  report and tripwire cannot disagree. Every marker must be load-bearing (an ablation test fails if
  removing one costs no recall), and the false-positive budget is **zero** — the tempting wrong fix is
  to delete the benign sample that fails.
- **The docs' claims about the code are machine-checked** in `tests/test_repo_hygiene.py`: links
  resolve, `make` targets exist, the decision ids the README cites are links, and cited tests exist.
  `AGENTS_LEARNING.md` is deliberately exempt. Add the check in the same commit as the claim.
- **`make ci` is the single definition of the gates** — `lock-check check eval markers smoke`, in
  order, stopping at the first failure. `.github/workflows/ci.yml` runs `uv sync --frozen` then
  `make ci` and nothing else, and `tests/test_ci_contract.py` fails if a gate moves. `--frozen`
  matters: without it a stale `uv.lock` is silently re-resolved instead of reported.
- **Do not pin `UV_PYTHON` in the Makefile.** `uv` reads `.python-version`; a `command -v python3`
  default breaks CI, where the runner's `python3` may be older.
- **`AGENTS_LEARNING.md` is maintained as we go.** Append `### YYYY-MM-DD — title` whenever you learn
  something that would change your next move; never rewrite an entry — append a superseding one.
  `tests/test_repo_hygiene.py` enforces that it exists, is reachable from `AGENTS.md` and `README.md`,
  and is dated.
- **`AGENTS.md` has an 8,000-byte budget** and sits close to it. The loader cuts the tail silently, so
  move detail into `docs/` or into this file rather than growing the spec.
- **A new failure class needs a test that forces it** —
  `tests/test_taxonomy.py::test_every_failure_class_has_a_test`.
- **`tests/helpers.py` is not collected** (no `test_` prefix): `execute()`, `make_config()`, and the
  turn builders `text`, `tool_call`, `refusal`.
- **`evals/` is the only authority on whether a change helped.** `make eval` gates at `1.0`; record the
  score in the commit message when it moves.
- **`docs/decisions/` is append-only** — one file per decision, numbered, never rewritten.
- **Never quote a test or eval count from memory.** Run it, or read `REGISTER.md`.

## Traps already paid for

- `Budget.check(include_steps=False)` when charging usage: the step bound gates *starting* a step, so
  applying it afterwards aborts a run that was already affordable.
- pydantic: `validation_alias` alone does not drive serialization — a field that must survive
  `model_dump` → `model_validate` needs `alias=` too. Dump configurations **by alias**.
- pytest's scratch space defaults outside the repo, which some sandboxes deny:
  `--basetemp=.pytest-tmp` in `pyproject.toml`. Do not remove it.
- Replay needs the tool **descriptors** from the trace, not rebuilt from the catalogue — they are
  inside every prompt hash.
- The loop finishes exactly once: use `_note_stop` from the paths that hand a reason back, and let the
  loop call `_finish`. `_stop(...).reason` double-emits `run_finished`.
- **A threshold is only a tripwire if the corpus makes it sharp.** A 5% false-positive budget on a
  26-sample corpus tolerates exactly one failure, so it never bit. A rule needs a *corpus sample*, not
  just a unit test.
- **The sandbox sometimes runs a command twice.** A `mv` that succeeds then errors breaks an `&&` chain
  *after* the work is done, a deleted file reappears, and a first `git push` reports "Everything
  up-to-date". Make mutating commands idempotent, and confirm with `git ls-remote`.
- **`cp` is aliased to `cp -i` here** — a restore silently prompts and does nothing. Use `/bin/cp -f`.
- **An apostrophe inside a single-quoted shell string ends it** — `unmatched "`. Write JSON to a file
  and pass `-d @file`.
- **Verifying one commit's tree in isolation needs the later commits' *untracked* files held aside
  too**, not just the modified ones.
- **A mutation-probe harness lies in five ways**: blind (`-qq` suppresses the summary it parses), a
  self-test caught by a *different* test file, a stray file from a killed run, a one-step mutation of
  a *generator* (a generated page needs mutate → regenerate → run), and — the worst — reporting
  `MISSED` when it could not read the result at all. Purge `__pycache__` on both sides, and **give it
  a third verdict, `UNREADABLE`**, for pytest exiting non-zero with no `FAILED` line it parsed.
  Detail: the `derived-register` skill, §6.
- **`-o addopts=` is for a *collection* call only.** It strips `--basetemp=.pytest-tmp`, so any
  command that *executes* tests then writes scratch space to the system temp dir, which this sandbox
  denies — every test errors, no `FAILED` line appears, and a probe reads that as MISSED. Collection
  (`tests/register.py`) is safe: no `tmp_path`, no scratch space.
- **Tool calls in one turn are dispatched sequentially, and the budget depends on it.**
  `Budget.allocate` gives each child a share of what is *left*, so a concurrent dispatch would let two
  spawns in one turn allocate from the same untouched remainder and multiply a declared bound —
  [0015](docs/decisions/0015-cost-budget-must-bind.md) by the back door. Pinned by
  `test_tool_calls_in_one_turn_are_run_one_at_a_time`, and `spawn_agent`'s description must not say
  "parallel" (`test_the_description_does_not_promise_concurrency`). **A description sent to a model is
  a claim the runtime makes** — the one kind that cannot be checked by reading either the doc or the
  code.

## Environment and operations

Python 3.13.12 and `uv` live outside the repo (`~/.workbuddy-ai/binaries/python/envs/default/bin/uv`);
`uv sync --all-extras` creates `.venv` inside it.

Remote: `https://github.com/Philosopher0707/agent-runtime`, **public** since 2026-09-27. `main` is
**protected** — the `gates` check required, `strict: true`, `enforce_admins: true` — so everything goes
branch → PR → green check → merge. `gh` is not installed: use the API, with the OAuth token from the OS
keychain; `git push` works through the `osxkeychain` helper. **Never write a token into `.git/config`
or a remote URL.**

**Squash is the only merge method with rebase disabled** (`allow_squash_merge` and
`allow_merge_commit` true, `allow_rebase_merge` false), and `delete_branch_on_merge` is on. Landing a
deliberate multi-commit split means enabling rebase temporarily (restore it in a `finally`, and **say
out loud that you changed a repo setting**) or re-landing as N PRs. After a rebase merge the SHAs are
rewritten: compare **trees** not SHAs (`git diff --stat <old-tip> <new-main>` must be empty) before
`git branch -D` — and `git branch -d` refusing with "not fully merged" is correct, not a problem. The
workflow is the `landing-separable-commits` skill.

`.workbuddy-ai/` is **gitignored** — workspace scratch, not project source. The deliberate record is
`AGENTS_LEARNING.md`, which is tracked, and the durable knowledge is this file, which is tracked too.
Both memory files still exist in the repository's earlier commits, so they are visible in the public
history.
