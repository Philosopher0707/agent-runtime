# 0030 — Starting a run from inside a run

Date: 2026-09-28
Status: accepted

## Context

The first genuinely new mechanism the agent layer needs. A tool that starts a run, so a task can
be delegated. [Decisions/0028](0028-the-agent-is-a-capability.md) settled the four questions
*before* this existed — budget, trace, confirmation, failure — precisely so that the
implementation would be mechanical.

It mostly was. It is also where the first real defect of the agent layer was found.

## What was built

`tools/subagent.py` — `spawn_agent`, a gated side effect that hands a self-contained task to
another configuration.

**The tool does not start the run, and cannot.** `runtime/` is where runs are started, and a tool
importing the loop would invert the layering that keeps the core general. So the tool takes a
`SpawnRunner` — a callable the composition root supplies — and the core supplies it **without
ever naming the tool**:

```python
# in the tool, declaring what it needs by *parameter* name
needs: ClassVar[frozenset[str]] = frozenset({"runner"})
```

The catalogue sees "this factory wants a `runner`" and passes one. It does not see `spawn_agent`.
That is what lets the delete test keep passing while a runtime facility reaches a tool, and it is
the mechanism any future tool with a runtime need will use.

## Verified against the four answers, live

```
PARENT   status=ok  steps=4  cost=$0.0174  tools=[list_sources, spawn_agent, spawn_agent]
  ├─ bd77325731674139  status=ok  steps=4  cost=$0.0052
  └─ b8032b9671b747a5  status=ok  steps=3  cost=$0.0049
```

1. **Budget** — each child got a share of what the parent had left, and the parent's cost is its
   own spend plus theirs. Not the parent's budget handed out twice.
2. **Trace** — each child wrote its own file; the parent named both trace ids in its `tool_call`
   record. No new trace event was needed: the existing one carries everything required to find
   the child, which is why the trace schema did not change.
3. **Confirmation** — off by default, and *observably* so: a child that tries to act with no
   inherited token ends `refused`, and the same child with one line changed in a configuration
   ends `ok`.
4. **Failure** — a child that cannot finish raises `ToolError`, and the existing taxonomy decides
   what it means. Seen working: a child that ran out of its allocated steps came back `partial`,
   and the parent continued as `degraded` because the spawn is optional.

The parent also wrote **self-contained tasks** for its children — *"You are fact-checking two
claims about a product called 'Northwind Sync'…"* — which is the behaviour the tool's description
asks for, not something the runtime could enforce.

## The defect: replay understates a spawned run's cost

**Found by testing the claim rather than asserting it.** Replaying the parent into a directory
that does not contain the children's traces:

```
replay reproduces identically: False
replayed cost: $0.0073    recorded cost: $0.0174
```

`$0.0073` is the parent's *own* spend. The children's `$0.0101` is missing.

**The cause is structural, not a slip.** On replay the spawn is served from the parent's recorded
outcome — the children are not re-run, which is correct and is the whole point — so
`Budget.allocate` is never called and `Budget.charge` never runs. The parent's cost is
reconstructed from a path that only executes when a child actually runs.

This is the same class of defect this project keeps finding: **a path that only executes for real
was never exercised by replay.** The repair pass was the last one. This is the second.

## What this decision does not do, and why

**It does not fix it, and it does not promote a fixture until it is fixed.** The fix is a
mechanism, not a patch: a tool must be able to report the budget it consumed, and the *record* of
that consumption must be what replay restores — because the charge has to come from the trace, not
from re-running the child. That means `ToolCallRecord` gains a spend, and the loop charges it when
it interprets the record, on both paths.

That is a schema change with a doc to update and its own tests, and doing it in the same commit as
the mechanism would bury it. **A sweep fixture is deliberately not committed**, because a fixture
whose replay diverges is a failing test dressed as coverage — and `tests/test_recorded_runs.py`
would correctly refuse it.

## Consequences

- `tools/subagent.py`, with `needs` as the general way a tool declares a runtime facility.
- `Budget.allocate` and `Budget.charge`. The allocation is computed from what *remains*, so a
  sequence of spawns cannot oversubscribe. A **reservation** would be needed for concurrent
  children; there are none, and the code says so rather than implying otherwise.
- `SpawnRequest`, `SpawnResult` and `SpawnRunner` live in `runtime/schemas.py`, not in the tool —
  starting a bounded run *is* what this runtime does, so the mechanism is a primitive and the
  judgement is a capability.
- `configs/verify_sweep.yaml`, the first configuration that delegates.
- **`spawn_agent` is a side effect, and I had that wrong first.** I declared it
  `side_effect=False, idempotent=False` — nothing changes, but a retry runs a second child. The
  registry refused it: *"a tool with no side effect is idempotent by definition"*. The registry is
  right, and for a reason worth keeping: delegating spends the caller's budget on a task the
  caller did not specify, and the principal decides that. Two gates, both needed — this one
  authorises *delegating*, the child's own authorises *acting*.
