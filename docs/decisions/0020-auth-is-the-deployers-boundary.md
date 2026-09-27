# 0020 — No auth on `POST /run`, and that is a position with an owner

Date: 2026-09-27
Status: accepted

## Context

`POST /run` has no authentication. It was documented — in `README.md` and
`docs/architecture.md`, both under known limitations — and it was **decided nowhere**. Not in
the roadmap, not in a decision.

That is the actual problem. Every other boundary in this project has a rule attached: the
trust model ([0009](0009-trust-model.md)), the untrusted-content policy
([0006](0006-untrusted-content-policy.md)), the confirmation gate, the redaction boundary
([0012](0012-pii-context-and-redaction.md)). This was the only one stated as a fact with
nobody responsible for it — and **the difference between "no auth by design" and "no auth yet"
is entirely a question of who owns it.**

## Decision

**The runtime is a library. Authentication is the deployer's boundary.**

Three things follow, and the first is the one that makes the other two safe:

1. **The service binds loopback by default** (`AGENT_HOST=127.0.0.1`). Out of the box it is
   not reachable off-host, so the default is not a service left open — it is a service a
   deployer must *choose* to expose. A test asserts the default rather than trusting it.
2. **Whoever exposes it owns what sits in front.** A reverse proxy, an API gateway, a private
   network, mTLS. None of those belong in a runtime whose job is to be embedded.
3. **The runtime will not grow auth.** No API keys on `/run`, no sessions, no accounts. That
   is a non-goal, not a backlog item, so nobody waits for it.

**Why not build it:** auth means choosing a scheme (bearer? mTLS? OIDC?) and a user model, and
every embedder would then have to undo it. A generic runtime with opinions about identity is
no longer generic. The same reasoning that keeps vendor strings out of the loop keeps identity
out of it.

## The consequence, stated loudly

**`confirmation_token` is not an authentication mechanism, and must never be mistaken for
one.** It stops the *model* from authorising a side effect — structurally, by overwriting a
field the model never sees. It does nothing about the *caller*.

A deployer who exposes `POST /run` without auth has given every reachable caller the ability to
authorise side effects, because the caller supplies the token. The gate protects against a
confused model, not a hostile caller. Those are different threats and only one is addressed.

## If a deployer needs per-caller identity

**Wrap `run_task`, do not extend it.** The composition root is the seam: it takes a
`RunRequest` and returns a `RunOutput`, and a caller can put whatever it likes in front of it
— a token check, a rate limit, an audit log, a tenant lookup — without the runtime knowing.

Adding a field to `RunRequest` would mean the runtime has an opinion about identity, which is
exactly what this decision refuses.

## Consequences

- The known-limitation entries stay, but they now point here rather than standing alone. A
  limitation with a decision behind it is a boundary; one without is a hole.
- Exposing the service is a deliberate act with a documented responsibility attached.
- The trigger to revisit: **shipping a hosted service rather than a library.** That would be a
  different product with a different decision, and it should not be smuggled in as a feature.

## Alternatives considered

**Add a bearer token to `POST /run`.** Rejected: it would be a *bad* auth scheme presented as a
good one, and it would imply the runtime had thought about identity when it had only thought
about one header. A deployer with real requirements would replace it anyway.

**Leave it documented as a limitation.** Rejected — that is the status quo, and it is precisely
what left the boundary unowned. The documentation was accurate and the responsibility was
unassigned.
