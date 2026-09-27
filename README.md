# agent-runtime

A general-purpose agent runtime: the loop, the tool boundary, the budget enforcer, the
tracer, and the evaluation harness. Not a product, not a vertical agent.

"General purpose" means **the runtime is generic and every capability is a
configuration.** A configuration is a named bundle of a system prompt, a tool set, a
model, and an eval suite. Adding a capability means adding a file under `configs/` —
never a branch in the core.

## Quickstart

```bash
make install          # uv sync --all-extras, writes uv.lock
make check            # ruff + pytest
make eval             # golden set, prints the score, non-zero below threshold
make smoke            # boots the service, POSTs one run, asserts 200 + schema

make run              # the CLI against configs/default.yaml
```

`configs/default.yaml` uses a scripted model, so everything above runs with **no API key
and no network**.

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
  fail-closed.
- **A mutating tool cannot run without a confirmation token** supplied by the caller. The
  model is not even told the field exists, and if it supplies one anyway it is
  overwritten.
- **Every run emits one append-only trace, and the trace alone reconstructs the run.**
  Replay verifies each rebuilt prompt hash and never executes a tool.

## Adding a capability

1. Write the tool in `tools/`, name it in `tools/builtin.py`. If it changes state,
   declare `side_effect = True` and a required `confirmation_token` field — registration
   refuses it otherwise.
2. Add `configs/<name>.yaml`. All four budget bounds are mandatory.
3. Add cases in `evals/cases/` that force the failure classes it can hit.
4. `make check && make eval`. If the score moves, record it in the commit message.

## Layout

```
runtime/      the loop, budget, trace, replay, schemas, status vocabulary, composition root
providers/    the Provider protocol, a stub, an OpenAI-compatible adapter, a replayer
tools/        the tool contract and dispatch policy, the built-ins, a scripted double
context/      context assembly, truncation, and untrusted-content handling
evals/        the golden-set runner, the deterministic scorer, the judge seam
configs/      worked examples — the fastest way to understand the design
tests/        one test per failure class, plus the invariants
docs/         architecture and the decision record
```

## Where to read next

- `docs/architecture.md` — the component map, the authority boundaries, and the
  invariants with the tests that hold them.
- `docs/decisions/` — one file per decision, numbered, append-only. Read
  `0002` (status precedence), `0006` (untrusted content), and `0009` (the trust model)
  for the three that shape everything else.
- `configs/` — four worked examples, from the smallest complete configuration to the
  real provider.

## Known limitations

Stated rather than discovered later. Token accounting is a heuristic; a timed-out
in-process tool's thread keeps running; structured output validates a JSON Schema subset;
a wall-clock-bounded run only replays identically under a deterministic clock;
`openai_compat` is verified against a mock transport rather than a live endpoint. Each is
recorded with its reasoning in `docs/decisions/` and `docs/architecture.md`.
