"""The rollback tool: what it refuses, and what it computes.

The tool exists to make one operation safe and to make three mistakes impossible. Most of
its value is in the refusals, so most of these tests are about what it will not do.

The tests build real throwaway git repositories rather than mocking `subprocess`, because
the thing under test *is* the interaction with git — a mock would test the mock.
"""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scripts.rollback import Plan, RollbackError, plan, require_clean_tree


def run(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository with three commits, so a revert range has something in it."""
    run("init", "-q", "-b", "main", cwd=tmp_path)
    run("config", "user.email", "test@example.test", cwd=tmp_path)
    run("config", "user.name", "Test", cwd=tmp_path)
    for index in range(3):
        (tmp_path / f"file{index}.txt").write_text(f"content {index}\n", encoding="utf-8")
        run("add", "-A", cwd=tmp_path)
        run("commit", "-q", "-m", f"commit {index}", cwd=tmp_path)
    return tmp_path


def head(repo: Path, *, offset: int = 0) -> str:
    return run("rev-parse", f"HEAD~{offset}" if offset else "HEAD", cwd=repo)


# ------------------------------------------------------------------------ planning


def test_the_plan_covers_every_commit_after_the_target(repo: Path) -> None:
    target = head(repo, offset=2)  # the first commit
    result = plan(target, root=repo)
    assert len(result.commits) == 2
    assert result.short == target[:8]
    assert result.branch == f"rollback/to-{target[:8]}"


def test_commits_are_ordered_newest_first(repo: Path) -> None:
    """git revert processes them in the order given, so newest-first is what undoes cleanly."""
    target = head(repo, offset=2)
    result = plan(target, root=repo)
    assert result.commits[0] == head(repo)
    assert result.commits[-1] == head(repo, offset=1)


def test_a_short_sha_is_accepted(repo: Path) -> None:
    target = head(repo, offset=2)
    assert plan(target[:8], root=repo).target == target


def test_an_unknown_revision_is_refused(repo: Path) -> None:
    with pytest.raises(RollbackError, match="no such revision"):
        plan("deadbeefdeadbeef", root=repo)


def test_head_is_refused_because_there_is_nothing_to_do(repo: Path) -> None:
    with pytest.raises(RollbackError, match="is HEAD"):
        plan("HEAD", root=repo)


def test_a_revision_that_is_not_an_ancestor_is_refused(repo: Path) -> None:
    """Reverting a side branch would not produce the tree you asked for."""
    run("checkout", "-q", "-b", "side", cwd=repo)
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    run("add", "-A", cwd=repo)
    run("commit", "-q", "-m", "side commit", cwd=repo)
    side = head(repo)
    run("checkout", "-q", "main", cwd=repo)

    with pytest.raises(RollbackError, match="not an ancestor"):
        plan(side, root=repo)


def test_a_plan_describes_itself_without_changing_anything(repo: Path) -> None:
    target = head(repo, offset=2)
    before = head(repo)
    described = plan(target, root=repo).describe()
    assert "2 commit(s) to revert" in described
    assert f"rollback/to-{target[:8]}" in described
    assert head(repo) == before


def test_the_plan_is_frozen(repo: Path) -> None:
    """A plan is a statement about a moment; mutating it would be a lie."""
    result = plan(head(repo, offset=1), root=repo)
    assert isinstance(result, Plan)
    with pytest.raises(FrozenInstanceError):
        result.commits = []  # type: ignore[misc]


# ----------------------------------------------------------------------- refusals


def test_a_dirty_tree_is_refused(repo: Path) -> None:
    """A rollback from uncommitted changes cannot be reviewed or undone cleanly."""
    (repo / "file0.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(RollbackError, match="dirty"):
        require_clean_tree(root=repo)


def test_an_untracked_file_also_counts_as_dirty(repo: Path) -> None:
    (repo / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    with pytest.raises(RollbackError, match="dirty"):
        require_clean_tree(root=repo)


def test_a_clean_tree_is_accepted(repo: Path) -> None:
    require_clean_tree(root=repo)


# ------------------------------------------------------------------- what it does


def test_reverting_the_range_produces_the_target_tree(repo: Path) -> None:
    """The property that matters: after the revert, the tree matches the target."""
    target = head(repo, offset=2)
    expected = run("ls-files", cwd=repo)
    assert "file2.txt" in expected

    result = plan(target, root=repo)
    run("revert", "--no-edit", *result.commits, cwd=repo)

    files = run("ls-files", cwd=repo)
    assert "file2.txt" not in files
    assert "file1.txt" not in files
    assert "file0.txt" in files


def test_the_revert_does_not_rewrite_history(repo: Path) -> None:
    """Every previously recorded SHA must still resolve — that is the whole point."""
    target = head(repo, offset=2)
    originals = [head(repo, offset=offset) for offset in (0, 1, 2)]

    result = plan(target, root=repo)
    run("revert", "--no-edit", *result.commits, cwd=repo)

    for sha in originals:
        assert run("cat-file", "-t", sha, cwd=repo) == "commit"
    # And HEAD moved forward, not backward.
    assert head(repo) != originals[0]
    assert len(run("rev-list", "HEAD", cwd=repo).split()) == 5
