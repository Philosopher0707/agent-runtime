"""Roll back to an earlier revision, through the protected flow.

The spec asks for a rollback that is "one command, documented, and performed once before
you need it". With no deploy target, rollback does not mean redeploying an image — it means
making the tree match an earlier revision again, as **new commits**. See
``docs/decisions/0013-rollback-scope.md``.

Two properties matter more than the automation:

* **It never rewrites history.** `main` is protected and public. A rollback is a revert
  commit on a branch, which goes through the same required check as anything else. A
  force-push would "work" and would also break every clone and every recorded SHA.
* **It refuses to guess.** A dirty tree, an unknown revision, or a revision that is not an
  ancestor of HEAD is an error, not something to work around.

    make rollback REV=<sha>              # revert everything after <sha>
    make rollback REV=<sha> ARGS=--dry-run

Set ``GH_TOKEN`` and it also opens the pull request; without it, the branch is pushed and
the compare URL is printed. It never merges — a rollback is worth a human looking at.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class RollbackError(Exception):
    """The rollback cannot be performed as asked."""


@dataclass(frozen=True)
class Plan:
    target: str
    short: str
    commits: list[str]
    branch: str

    def describe(self) -> str:
        lines = [f"roll back to {self.short} ({len(self.commits)} commit(s) to revert)"]
        lines.extend(f"  revert {sha[:8]}" for sha in self.commits)
        lines.append(f"  on branch {self.branch}")
        return "\n".join(lines)


def git(*args: str, root: Path = REPO_ROOT, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RollbackError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def plan(target: str, *, root: Path = REPO_ROOT) -> Plan:
    """Validate the target and compute what would be reverted. Changes nothing."""
    resolved = git("rev-parse", "--verify", f"{target}^{{commit}}", root=root, check=False)
    if not resolved:
        raise RollbackError(f"no such revision: {target!r}")

    head = git("rev-parse", "HEAD", root=root)
    if resolved == head:
        raise RollbackError(
            f"{target!r} is HEAD; there is nothing to roll back. "
            f"Name the revision you want the tree to match again."
        )

    # `git merge-base --is-ancestor` answers with an exit code, not with output, so it
    # needs its own probe rather than the `git()` helper.
    probe = subprocess.run(
        ["git", "merge-base", "--is-ancestor", resolved, "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise RollbackError(
            f"{target!r} is not an ancestor of HEAD, so reverting it would not produce the "
            f"tree you asked for. Rolling *forward* past a merge is a different operation."
        )

    commits = git("rev-list", f"{resolved}..HEAD", root=root).split()
    if not commits:
        raise RollbackError(f"no commits between {target!r} and HEAD")

    return Plan(
        target=resolved,
        short=resolved[:8],
        commits=commits,
        branch=f"rollback/to-{resolved[:8]}",
    )


def require_clean_tree(*, root: Path = REPO_ROOT) -> None:
    if git("status", "--porcelain", root=root):
        raise RollbackError(
            "the working tree is dirty. A rollback that starts from uncommitted changes "
            "cannot be reviewed, and cannot be undone cleanly if it goes wrong."
        )


def ensure_branch_free(branch: str, *, root: Path = REPO_ROOT) -> None:
    """Refuse if a rollback branch is already present.

    Found by performing the rollback once. A run interrupted part-way — after the branch
    was created and the revert committed, before the push — leaves that branch behind. A
    second run would then plan from the *new* HEAD, treat the previous revert as a commit
    to revert, and quietly produce the opposite of what was asked for. Refusing is the only
    safe answer; the operator can merge it, delete it, or resume by hand.
    """
    if git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", root=root, check=False):
        raise RollbackError(
            f"branch {branch!r} already exists. A previous rollback to this revision is "
            f"either still open or was abandoned part-way. Merge it, delete it, or resume "
            f"it by hand — re-running here would plan the revert as something to revert."
        )


def pull_request_url(plan_: Plan, *, root: Path = REPO_ROOT) -> str:
    remote = git("remote", "get-url", "origin", root=root)
    slug = remote.removesuffix(".git").split("github.com", 1)[-1].lstrip(":/")
    return f"https://github.com/{slug}/compare/main...{plan_.branch}?expand=1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roll back to an earlier revision.")
    parser.add_argument("--to", dest="target", required=True, help="revision to match again")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument("--root", default=str(REPO_ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root)

    try:
        plan_ = plan(args.target, root=root)
        if args.dry_run:
            # A dry run is for inspecting the plan, so it reports even when the branch is
            # in the way — but it says so, because the plan is not actionable as it stands.
            print(plan_.describe())
            if git(
                "rev-parse", "--verify", "-q", f"refs/heads/{plan_.branch}", root=root, check=False
            ):
                print(f"\nnote: branch {plan_.branch!r} already exists, so this plan cannot run.")
            return 0
        require_clean_tree(root=root)
        ensure_branch_free(plan_.branch, root=root)
    except RollbackError as exc:
        print(f"rollback refused: {exc}", file=sys.stderr)
        return 1

    print(plan_.describe())
    git("checkout", "-b", plan_.branch, root=root)
    print(f"\nreverting {len(plan_.commits)} commit(s)...")
    git("revert", "--no-edit", *plan_.commits, root=root)

    print("\nrunning the gates before pushing...")
    gates = subprocess.run(["make", "ci"], cwd=root, check=False)
    if gates.returncode != 0:
        print(
            f"\nthe gates failed after the revert, so nothing was pushed. The revert is on "
            f"branch {plan_.branch}; inspect it with `git revert --abort` or fix forward.",
            file=sys.stderr,
        )
        return 1

    git("push", "-u", "origin", plan_.branch, root=root)
    url = pull_request_url(plan_, root=root)
    print(f"\npushed {plan_.branch}")

    token = os.environ.get("GH_TOKEN")
    if not token:
        print(f"open the pull request: {url}")
        print("(set GH_TOKEN to have this step done for you)")
        return 0

    import json
    import urllib.request

    slug = pull_request_url(plan_, root=root).split("github.com/")[1].split("/compare")[0]
    payload = json.dumps(
        {
            "title": f"Roll back to {plan_.short}",
            "head": plan_.branch,
            "base": "main",
            "body": (
                f"Reverts {len(plan_.commits)} commit(s) to bring the tree back to "
                f"`{plan_.short}`.\n\n"
                + "\n".join(f"- `{sha[:8]}`" for sha in plan_.commits)
                + "\n\nGenerated by `make rollback`. History is not rewritten; this is a "
                "revert commit, so every previously recorded SHA still resolves."
            ),
        }
    ).encode()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{slug}/pulls",
        data=payload,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            created = json.load(response)
        print(f"opened PR #{created['number']}: {created['html_url']}")
    except Exception as exc:
        print(f"could not open the pull request ({exc}); open it here: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
