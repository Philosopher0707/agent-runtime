# 0013 — Rollback means reverting a revision, because nothing is deployed

Date: 2026-09-27
Status: accepted

## Context

The spec's deploy section ends with a requirement that presupposes a deployment:

> Rollback: one command, documented, and performed once before you need it — an untested
> rollback is not a rollback.

This project has no deploy target. It is run locally, and it is going to stay that way until
it is worth deploying. So the requirement, as written, has nothing to point at: there is no
image to redeploy and no previous version to switch back to.

That leaves three honest options, and only one of them produces something real.

## Decision

**Rollback means bringing the tree back to an earlier revision, as new commits, through the
same protected flow as any other change.**

`make rollback REV=<sha>` reverts every commit after `<sha>` on a branch, runs `make ci`,
pushes, and opens a pull request. It never merges — a rollback is worth a look before it
lands.

Three properties are load-bearing:

- **It never rewrites history.** A `git reset --hard` plus a force-push would "work" and
  would also invalidate every SHA recorded in this repository's own documents, break every
  clone, and do it on a protected public branch. A revert commit leaves all of that intact
  and is reviewable.
- **It refuses to guess.** A dirty working tree, an unknown revision, or a revision that is
  not an ancestor of HEAD is an error. Each of those would otherwise produce a rollback that
  looks like it worked and did not.
- **It runs the gates before pushing.** A revert can conflict with later work, and a revert
  that breaks the build is worse than the thing it was undoing.

## Consequences

- **The spec's requirement is met in the only sense available.** "Performed once before you
  need it" is satisfied by performing it once — which was done, against a real revision, with
  the full path exercised.
- **This decision is superseded the moment a deploy target exists.** A redeploy rollback is a
  different mechanism with different failure modes (a bad image, a failed health check, a
  schema change that does not reverse). The trigger to revisit is the first deployment, not
  a feeling.
- Reverting a merge commit is possible but awkward, and `git revert` will say so. The tool
  does not try to be clever about it.
- The tool needs a `GH_TOKEN` to open the pull request; without one it pushes the branch and
  prints the compare URL. It does not read credentials from the environment implicitly.

## Alternatives considered

**Defer the requirement entirely.** Rejected: the spec names it, and a revert-through-the-
gates path is genuinely useful even locally — the alternative people reach for under pressure
is `git reset --hard`, which is the one operation that must not happen here.

**Add a deploy target so rollback means redeploying.** The only reading where the requirement
is met as written. Rejected for now as premature: it would mean choosing a host and a release
process for a project that has never been run against a real model. Recorded in
`docs/roadmap.md` as the thing that supersedes this decision.

**Document `git revert` and stop there.** Rejected: "one command, documented" is a real
requirement, and the difference between a documented `git revert` and a tool is that the tool
also refuses a dirty tree and runs the gates. The refusal is most of the value.
