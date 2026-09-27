"""Which build produced a run.

`run_started` records `git describe --always --dirty`. The point is the question it makes
answerable *after a rollback*: which traces came from the code that was rolled back?

Without it that question has no answer, and it is the one place where reverting the
repository and reverting the system genuinely diverge — `make rollback` reverts git, and the
trace store is gitignored, so it is left exactly as it was.

Two properties are worth as much as the field itself:

* **It is recorded, never used.** The loop makes no decision from it, so two runs of the same
  task with different revisions must produce the same result.
* **A dirty tree says so.** A trace from an uncommitted tree does not correspond to any commit,
  and recording the hash alone would imply a reproducibility that is not there.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from runtime.factory import current_revision
from runtime.schemas import RunOutput
from runtime.trace import TraceWriter, new_trace_id, read_trace
from tests.helpers import execute, make_config, text


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """`current_revision` is cached, because a process has one revision."""
    current_revision.cache_clear()
    yield
    current_revision.cache_clear()


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git("init", "-q", "-b", "main", cwd=tmp_path)
    git("config", "user.email", "t@example.test", cwd=tmp_path)
    git("config", "user.name", "T", cwd=tmp_path)
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "first", cwd=tmp_path)
    return tmp_path


# ------------------------------------------------------------- resolving it


def test_a_repository_reports_a_revision(repo: Path) -> None:
    revision = current_revision(str(repo))
    assert revision
    assert len(revision) >= 7


def test_a_dirty_tree_says_so(repo: Path) -> None:
    """A trace from an uncommitted tree must not claim to be a commit."""
    clean = current_revision(str(repo))
    assert clean and not clean.endswith("-dirty")

    (repo / "f.txt").write_text("changed\n", encoding="utf-8")
    current_revision.cache_clear()
    assert current_revision(str(repo)).endswith("-dirty")


def test_no_repository_is_not_an_error() -> None:
    """This is a library. It may be installed from a wheel with no git metadata at all.

    Note why this cannot use ``tmp_path``: `pyproject.toml` sets `--basetemp=.pytest-tmp`, so
    pytest's scratch space is *inside this repository* — a deliberately documented setting —
    and `git describe` correctly walks up and finds it. Testing absence needs a directory
    genuinely outside any repo.
    """
    outside = Path(tempfile.mkdtemp())
    try:
        assert current_revision(str(outside)) is None
    finally:
        outside.rmdir()


def test_the_environment_overrides_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """For a container that has the revision baked in at build time."""
    monkeypatch.setenv("AGENT_REVISION", "built-from-a-tarball")
    assert current_revision(str(tempfile.mkdtemp())) == "built-from-a-tarball"


def test_an_empty_override_falls_through_to_git(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    monkeypatch.setenv("AGENT_REVISION", "   ")
    assert current_revision(str(repo)) == git("describe", "--always", "--dirty", cwd=repo)


# --------------------------------------------------------------- recording it


def test_the_revision_is_recorded(tmp_path: Path, tracer) -> None:
    execute("go", config=make_config(), tracer=tracer, script=[text("done")], revision="abc1234")
    recorded = read_trace(tracer.path).first("run_started").payload
    assert recorded["revision"] == "abc1234"


def test_no_revision_records_as_none(tmp_path: Path, tracer) -> None:
    """Outside a repository it is None, and that must be a value rather than a crash."""
    execute("go", config=make_config(), tracer=tracer, script=[text("done")])
    assert read_trace(tracer.path).first("run_started").payload["revision"] is None


def test_a_trace_without_the_field_still_reads(tmp_path: Path) -> None:
    """Older traces predate it, and `payload` is schemaless, so the *key is absent* rather
    than null. Both mean "no build recorded", which is the honest answer for those runs."""
    import json

    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps({"ts": "a", "trace_id": "t", "event": "run_started", "payload": {"task": "x"}})
        + "\n",
        encoding="utf-8",
    )
    assert read_trace(path).first("run_started").payload.get("revision") is None


# ------------------------------------------------------- recorded, never used


def test_the_loop_makes_no_decision_from_it(tmp_path: Path) -> None:
    """It is provenance, not input. Two revisions must produce the same run."""
    config = make_config()

    def one_run(revision: str | None) -> RunOutput:
        with TraceWriter(tmp_path / f"t-{revision}", new_trace_id()) as tracer:
            return execute(
                "go", config=config, tracer=tracer, script=[text("done")], revision=revision
            )

    assert one_run("aaaaaaa").canonical() == one_run("bbbbbbb").canonical()
