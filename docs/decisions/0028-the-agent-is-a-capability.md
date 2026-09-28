# 0028 — The agent is a capability, not the core

Date: 2026-09-28
Status: accepted

## Context

The project is building an agent on top of the runtime — something that decomposes a task,
chooses tools, delegates, and persists across runs. This is a deliberate change of shape: the
runtime has been the whole project, and now it is the substrate for something larger.

That is a fine thing to do. It is also the moment the runtime is most likely to stop being
general, because **every new capability makes the runtime want to know about it.** A planner wants
to be in the loop. Memory wants to be automatic. Sub-agents want the runtime to compose their
traces. Tool selection wants to be a runtime concern. Each is individually reasonable and each is
a violation of the one rule.

So the boundary is written **first**, before any agent code exists, because a boundary written
after the first exception is a description of the exceptions.

## Decision

**The agent lives in `configs/` and `tools/`. The runtime is unchanged.**

Three parts:

1. **The runtime provides primitives; a configuration composes them.** The agent is a
   configuration that uses a planning prompt, plus tools that do things the runtime has never
   heard of. Nothing about planning, memory, delegation, or tool selection enters `runtime/`.
2. **The delete test is the enforcement, and it is already a tripwire.**
   `tests/test_repo_hygiene.py::test_the_core_imports_no_capability` fails if any file in
   `runtime/` imports past the seam (`tools.catalogue`, `tools.registry`, `tools.builtin`). The
   agent must pass the test the triage domain passes: **delete the capability and its one line in
   the seam, and the runtime still works.**
3. **The planner and sub-agent spawning are one mechanism, not two.** Both are "a tool that
   starts a run". Building them as two things would produce two budget stories and two trace
   stories for the same operation.

## The four questions a spawned run raises, answered

A tool that starts a run is the first genuinely new thing, and it raises four questions the
existing design does not answer. Answering them *before* writing the tool is the point of this
record — the tool is mechanical once they are settled.

### 1. Budget — hierarchical, allocated at spawn, and returned

The parent allocates a portion of **its own remaining budget** to the child at spawn time. The
child cannot exceed its allocation. Whatever the child does not spend returns to the parent.

- **Not "each child gets the parent's full budget"** — that makes a declared bound multiply, which
  is the same defect [decisions/0015](0015-cost-budget-must-bind.md) refuses at the configuration
  level: a bound that does not bound what it names.
- **Not "split evenly in advance"** — the parent does not know how many children it will need.
- The allocation is a *reservation*, so the parent cannot oversubscribe. A parent with $1 that
  spawns three children allocating $0.50 each must be refused, not silently overcommitted.

### 2. Trace — the child's trace is its own file, linked by id

The child writes its own trace, with its own `trace_id`. The parent records `subagent_started` and
`subagent_finished` events carrying the child's `trace_id` — **not** the child's steps.

- **Not interleaved into the parent's file.** Traces are append-only and one writer per file; a
  child writing into the parent's trace breaks both.
- **Not merged at the end.** A merged trace would make replay depend on the merge being
  deterministic, and the child's steps were not the parent's.
- Replay composes them recursively: replaying the parent replays the *spawn tool call* from its
  recorded outcome, and the child's trace is replayed as its own run.

### 3. Confirmation — a child does **not** inherit the caller's token

A side effect inside a child run needs a token, and **the parent agent cannot supply one.** The
gate exists so the *principal* decides whether a side effect happens; a sub-agent's caller is the
parent, not the principal, and the principal authorised a task it has not seen decomposed.

So: **no inheritance by default.** A configuration may declare that its children inherit, and that
declaration is the thing a reviewer reads — which is the same shape as every other gate here: the
default is closed, and opening it is explicit and visible.

### 4. Failure — a child's failure is a tool outcome, and nothing new

If a spawned run fails, that is a `ToolCallRecord` with a non-`ok` outcome, exactly like any other
tool. The existing taxonomy does the rest: `optional: false` makes the parent `partial`, optional
makes it `degraded`, and the child's own trace says why.

**No new mechanism, and that is the design working.** A spawned run is a tool call whose result
happens to be another run.

## What this does not decide

Named so this record is not read as more than it is:

- **Memory.** Whether it is a tool, a layer, or a hybrid is open. The constraint is that it is
  **untrusted on read** — anything remembered is content the runtime did not write, and it enters
  the envelope like any other.
- **Tool selection at scale.** Premature until a toolset is large enough that selection is a real
  problem. The runtime's current answer — send every schema — is correct until it is not.
- **A durable scheduler.** A rewrite of the trace model, and it should be done once, after the
  trace has stopped changing for other reasons.
- **Hierarchical budgets beyond one level.** The allocation rule above is stated for one level;
  a grandchild's allocation is its parent's to make, by the same rule, and that is enough until a
  case needs more.

## Consequences

- **The README's framing changes** — the project now builds an agent on a runtime, rather than
  only the runtime. It says so without claiming the agent exists, because it does not yet.
- **The roadmap gains the agent's order of work**, which is deliberately not the order the
  proposal suggested: it starts with the spawn tool, because that is what makes the other
  questions concrete.
- **The delete test stops being a nicety.** It is the thing that keeps a growing project from
  becoming a framework for one kind of agent, and it now runs on every push.

## Alternatives considered

**Build the agent into the runtime, since the runtime is ours.** Rejected: it is exactly how a
general runtime becomes a framework, and the project would lose the property that makes it worth
reading — that the core knows nothing.

**Decide the boundary later, once the shape is clear.** Rejected: the shape is never clear before
the first exception, and the first exception is always locally reasonable. This record exists to
be the thing a later change has to argue with.
