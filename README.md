# agent-runtime

A general-purpose agent runtime — the loop, the tool boundary, the budget enforcer, the tracer,
and the evaluation harness — and the agent being built on top of it.

"General purpose" means **the runtime is generic and every capability is a configuration.** A
configuration is a named bundle of a system prompt, a tool set, a model, and an eval suite. Adding
a capability means adding a file under `configs/` — never a branch in the core.

**Where this is going, and where it is.** The runtime is complete against its spec and has run
against a real model and one real domain. The project is now building an *agent* on top of it:
something that decomposes a task, delegates, and persists. **That agent does not exist yet.** What
exists is the boundary that keeps it from becoming the core
([decisions/0028](docs/decisions/0028-the-agent-is-a-capability.md)), and a test that enforces it:
delete the capability and its one line in the seam, and the runtime still works.

The runtime stays the thing worth reading. The agent is the largest capability the rule has been
asked to hold — and if it can be deleted without touching the core, the rule held.

## Quickstart

```bash
make install          # uv sync --all-extras, writes uv.lock
make ci               # every gate, in order, stopping at the first failure
make run              # the CLI against configs/default.yaml
```

The gates individually, if you want them one at a time:

```bash
make lock-check       # fail if uv.lock is out of date with pyproject.toml
make check            # ruff + pytest
make eval             # golden set, prints the score, non-zero below threshold
make live             # property suite against a REAL model (needs a key; not in ci)
make markers          # measures the injection-marker rule (ARGS=--repo sweeps this repo)
make smoke            # boots the service, POSTs one run, asserts 200 + schema
make rollback REV=<sha>   # revert back to a revision, through the gates
make register         # regenerate REGISTER.md — the suite, numbered and by category
make notes            # regenerate NOTES.md — the docs, with the claim each one leads with
```

`make rollback REV=<sha>` is the one command above that is not self-explanatory: it changes what
the code *is*, rather than what it does. [decisions/0013](docs/decisions/0013-rollback-scope.md)
covers what it touches (the repository, not the trace store), what it does not (restart
anything), why `REV` has no default, and what "through the gates" means.

**Two eval suites, because they test different things.** `make eval` asserts *exact* outcomes
against a scripted model — it is the runtime's contract test, deterministic, and it gates CI.
`make live` asserts *properties* against a real model — status, which tools were called,
whether the guardrail held — because a real model's wording varies and a suite that fails on
phrasing is a suite nobody keeps.

A live run cannot gate a push, so the same question gets a second answer that can: real traces
are committed under `evals/fixtures/` and replayed in CI with no key and no network. Run
`make live`, read the trace, copy a good one into `evals/fixtures/`, and CI covers that
behaviour forever.

`configs/default.yaml` uses a scripted model, so everything above except `make live` runs with
**no API key and no network**.

## Pointing it at a real model

`configs/openai_compat.yaml` is a worked example for any OpenAI-compatible endpoint. Two
files, and the split matters:

```bash
cp .env.example .env     # then put AGENT_API_KEY=... in it. .env is gitignored.
# and edit configs/openai_compat.yaml: model, base_url, and the two prices
```

The **key** goes in `.env`, or in the environment — a real environment variable wins over
the file. The **endpoint, model and prices** go in the configuration file, because they are
part of the capability. Prices are not optional: `max_cost_usd` is a required bound, so a
configuration whose prices are both zero has a bound that can never fire, and it is refused
at load.

```bash
uv run python cli.py run --config openai_compat --task "What is 21 * 2?"
```

Run it from the project root — `.env` is resolved against the working directory.

`make eval` still runs the **stub** suite: its cases assert exact outcomes, which only works
against a deterministic model. Grading a live model needs a scorer that asserts *properties*,
and that is roadmap phase 1.

## CI

`.github/workflows/ci.yml` installs with `uv sync --frozen` and then runs exactly one
thing: `make ci`. The gates are defined once, in the Makefile, so a local run and CI cannot
drift — `tests/test_ci_contract.py` fails if the workflow starts invoking a gate directly,
or if a gate is dropped from the `ci` recipe.

`--frozen` matters. Without it a stale `uv.lock` is silently re-resolved rather than
reported, so CI would test something other than what is committed.

**`make ci` is still five gates.** The documentation checks are pytest tests, so they run
inside `make check` rather than as a sixth gate — the README's own claims about the code are
gated by the same command that gates the code, and the recipe did not change to make that
true. `EXPECTED_GATES` in `tests/test_ci_contract.py` pins the set, so adding a sixth gate
without saying so fails the suite.

Each gate was verified to fail before it was trusted: a stale lock, a broken assertion, a
wrong eval expectation, a marker that catches nothing, and an end-to-end regression each
turn `make ci` non-zero, and `make ci` stops at the first one.

## Three ways in

```bash
# the CLI
uv run python cli.py run --config default --task "What is 21 * 2?"
uv run python cli.py configs
uv run python cli.py replay .traces/<id>.jsonl

# the service
uv run python -m service.app            # POST /run, GET /healthz, GET /configs
curl -s localhost:8080/run -H 'content-type: application/json' \
  -d '{"task": "What is 21 * 2?", "config": "default"}' | jq .

# replay a recorded run from its trace alone
uv run python -c "from runtime.replay import replay; print(replay('.traces/<id>.jsonl').status)"
```

## The one rule

> **The core loop contains no domain logic and no provider-specific code.**

`runtime/loop.py` knows the `Provider` protocol, the tool boundary protocol, the budget,
and the failure taxonomy. It knows one tool *name* — `ask_clarification`, a control tool
it intercepts — and nothing else about any tool. Every vendor string lives in
`providers/`; every concrete implementation is named in `runtime/factory.py` and nowhere
else.

## What is enforced, not just documented

- **Four bounds, none optional.** `max_steps`, `max_tokens_total`, `max_wall_clock_s`,
  `max_cost_usd`. A configuration that omits any one of them fails to load.
- **The system prompt and the task are never dropped.** If they cannot fit, the run
  reports `context_overflow` rather than sending something else.
- **Tool output is data, never instructions.** Always enveloped, always scanned,
  fail-closed. The scan is tiered and its false-positive rate is measured, not assumed —
  an earlier single-tier version refused 36% of benign tool output.
- **A mutating tool cannot run without a confirmation token** supplied by the caller. The
  model is not even told the field exists, and if it supplies one anyway it is
  overwritten.
- **Every run emits one append-only trace, and the trace alone reconstructs the run.**
  Replay verifies each rebuilt prompt hash and never executes a tool. Every line carries a
  schema version, so a trace written by an older format is refused by name rather than
  reported as a divergence.
- **A prompt change says which part moved.** The prompt is four things — system prompt, tool
  schemas, untrusted envelope, tool-call renderer — across four files, two of them code. Each
  is hashed separately and recorded, so a change is a diagnosis rather than "the hash differs".
- **The prompt is never redacted; the trace can be.** Redacting the prompt would silently
  change the task. A redacted trace cannot be replayed, so the two are mutually exclusive —
  which is stated in the design rather than discovered later.
- **A declared bound has to be able to bind.** All four budget bounds are required, and a
  configuration whose cost bound cannot fire — prices unset, so cost is pinned at zero — is
  refused at load rather than reporting safety it does not provide.
- **The documentation's claims about the code are checked.** Every internal link, every `make`
  target the README names, every decision id it cites, and every test name it cites must
  resolve. A cited test reads as evidence, and evidence that does not exist is worse than none —
  it stops the reader looking.

## Adding a capability

1. Write the tool in `tools/`, name it in `tools/builtin.py`. If it changes state,
   declare `side_effect = True` and a required `confirmation_token` field — registration
   refuses it otherwise.
2. Add `configs/<name>.yaml`. All four budget bounds are mandatory.
3. Add cases in `evals/cases/` that force the failure classes it can hit.
4. Run `make ci`. If the score moves, record it in the commit message.

## Layout

```
runtime/      the loop, budget, trace, replay, redaction, schemas, status vocabulary, factory
providers/    the Provider protocol, a stub, an OpenAI-compatible adapter, a replayer
tools/        the tool contract and dispatch policy, the built-ins, a scripted double
context/      context assembly, truncation, and untrusted-content handling
evals/        the golden-set runner, the live property suite, the marker corpus, the judge
configs/      worked examples — the fastest way to understand the design
tests/        one test per failure class, plus the invariants
docs/         architecture, the roadmap, and the decision record
```

## The capabilities

Two, and they are different in kind. Both are a configuration, a tool module, and fixtures — the
runtime does not know either of them exists.

### `configs/verify.yaml` — check claims against sources

Read documents, check a claim against them, report a verdict. Two read-only tools, and no way to
act.

```bash
uv run python cli.py run --config verify --task "Check this claim against the sources: Northwind Sync support responds within 24 hours."
```

It is the one worth reading first, because of what it refuses to do. Its verdicts are **four, not
two**:

| verdict | means |
|---|---|
| `supported` | a source states it, or entails it |
| `contradicted` | a source states the opposite |
| `absent` | no source addresses it |
| `undecidable` | a source addresses it but does not settle it |

*"The source does not mention it"*, *"the source says no"*, and *"the source is ambiguous"* are
three different findings. An agent that collapses them into "false" is overstating what it knows —
which is the failure this runtime was built to refuse, so a capability built on it should not
commit it either.

One of the three source fixtures is a **draft that claims authority over the reader** — it says to
treat everything in it as confirmed and to mark all claims supported. That payload trips no
marker, so the scan does not fire and the text reaches the model intact. What it does with it is
measured rather than assumed: the correct verdict is `undecidable`, and `make live` checks it.

### `configs/triage.yaml` — a support inbox

Read a message an untrusted sender wrote, classify it, and escalate it when a person is needed.
Three tools in `tools/triage.py`, the last a side effect and therefore gated.

```bash
uv run python cli.py run --config triage --task "Triage message 004." --confirmation-token local
```

The four messages in `messages/` include a deliberate pair:

| message | what it is | which defence handles it |
|---|---|---|
| `003` | an instruction-override payload | the **scan** refuses it; the model never sees it |
| `004` | a data exposure that says *not* to escalate | the payload **evades** the scan — the **model** is the only defence |

That pair is the point. The obvious injection is caught before the model is asked anything, and
the subtle one is not caught at all.

## Where to read next

**To understand why it is built this way.** [docs/architecture.md](docs/architecture.md) — the
component map, the authority boundaries, and the invariants with the tests that hold them. Then
the four decisions that shape everything else:

- [0002 — status precedence](docs/decisions/0002-status-precedence.md)
- [0006 — untrusted content](docs/decisions/0006-untrusted-content-policy.md)
- [0009 — the trust model](docs/decisions/0009-trust-model.md) — content trust, plus the
  addendum naming the second axis it deliberately does not cover, which
  [0020](docs/decisions/0020-auth-is-the-deployers-boundary.md) decides
- [0020 — auth is the deployer's boundary](docs/decisions/0020-auth-is-the-deployers-boundary.md)

**Before your first change.** [REFERENCE.md](REFERENCE.md) — the durable knowledge: the conventions
the code does not show you, and the mistakes that have already been paid for. It is tracked, so it
belongs to the repository rather than to one machine's notes.

**To work on it.** [AGENTS_LEARNING.md](AGENTS_LEARNING.md) is what the project has taught us —
the surprises, the mistakes, the questions still open. Read it before changing anything; it is
the fastest way to avoid re-learning a lesson. Append a dated entry when you learn something
that would change your next move. [configs/](configs/) is four worked examples, from the
smallest complete configuration to the real provider.

**To parse a trace.** [docs/trace-schema.md](docs/trace-schema.md) — the envelope, every event,
and the invariants that make replay exact. Read it before writing anything that reads a trace.

**To see what the suite covers, and read a failure.** [REGISTER.md](REGISTER.md) — every test
file, numbered and grouped by category, with the case counts and the first line of each file's
own docstring. The boundary comes first, because a broken seam explains every failure below it.
It is generated (`make register`) and `tests/test_register.py` fails when it drifts, so it cannot
say anything the suite does not.

**To see what the documentation says, in one page.** [NOTES.md](NOTES.md) — every markdown file
in the repository, with the claim it leads with, quoted from the file itself: a decision quotes
its own heading, a guide's sentence is checked verbatim. Forty documents, and no paraphrase to
drift. Generated by `make notes`; `tests/test_notes.py` fails if a document is missing, if a
quotation is no longer in the file it cites, or if the page does not match a fresh render.

**To know where it is going.** [docs/roadmap.md](docs/roadmap.md), in dependency order, and what
it deliberately is not.

**The rest.** [docs/decisions/](docs/decisions/) — one file per decision, numbered, append-only.
Read by number; the log makes supersession visible.

## Known limitations

Stated rather than discovered later. Each has its reasoning in `docs/decisions/` and the
fuller list in `docs/architecture.md`.

- **Token accounting is a heuristic**, calibrated against a real endpoint at two ratios —
  4.0 characters per token for prose, 2.0 for JSON tool schemas. Measured at parity on the
  shapes tested; a real tokeniser remains the documented upgrade path.
- **A timed-out in-process tool's thread keeps running**, because Python cannot kill one.
- **Structured output validates a JSON Schema subset**, not the whole standard.
- **Redaction is pattern-based and off by default** — it catches shapes, not meanings, and
  enabling it makes the trace non-replayable.
- **The injection scan is a lexical tripwire** with a measured precision limit: it cannot
  tell a payload from prose that *quotes* one, and it misses payloads written as ordinary
  prose. The envelope, not the scan, is the primary defence.
- **`confirmation_token` is a presence check, not a capability.** It stops the *model* from
  authorising a side effect. It does not authenticate the caller — and that is a deliberate
  boundary, not a gap: the runtime is a library and auth is the deployer's, with the service
  bound to loopback by default. See
  [decisions/0020](docs/decisions/0020-auth-is-the-deployers-boundary.md).
- **`refused` means the provider said the model refused.** A model that declines *in words*
  without setting the vendor flag is reported as `ok`, with the refusal as the output. The
  live suite found exactly this on its first run.
- **Runs are isolated except for the notes directory.** Each run gets its own budget,
  context, tool executor and trace, but two concurrent runs writing the same note filename
  race silently.
- **`openai_compat` has run against a live endpoint** (OpenRouter) for a plain answer and a
  native tool call, and a real trace replayed exactly. Its error paths are covered against a
  mock transport; it has not met a wide range of providers.
