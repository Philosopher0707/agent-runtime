"""The `.env` file: parsing, precedence, and the entry points that load it.

This exists because the file was implied and not implemented. `.env.example` and a
`.gitignore` entry for `.env` both said "put your secrets here"; nothing read it, so a key
placed there was silently ignored and the provider was called unauthenticated. The only
symptom was a 401 from the vendor, with nothing pointing at the cause.

The interesting tests are therefore the ones about *silence*: that a real environment
variable still wins, and that no entry point can forget to load the file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import ENV_FILE, load_config, load_env_file, parse_env_file, read_api_key

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Variables the fixtures below may set, so teardown always removes what a test added.
TOUCHED = (
    "AGENT_API_KEY",
    "AGENT_MODEL",
    "AGENT_BASE_URL",
    "PLAIN",
    "QUOTED",
    "SINGLE",
    "SPACED",
    "EXPORTED",
    "COMMENTED",
    "ALREADY",
    "KEY_WITH_HASH",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A clean slate for the variables these tests touch.

    ``load_env_file`` writes to ``os.environ`` directly, so monkeypatch cannot track what it
    adds — deleting each key first makes teardown remove it again.
    """
    for key in TOUCHED:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def write_env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------- the parser


def test_the_shapes_people_actually_write_are_parsed() -> None:
    parsed = parse_env_file(
        "\n".join(
            [
                "# a comment",
                "",
                "PLAIN=value",
                'QUOTED="a value with spaces"',
                "SINGLE='another value'",
                "export EXPORTED=exported-value",
                "SPACED = padded ",
                "COMMENTED=value # trailing note",
            ]
        )
    )
    assert parsed == {
        "PLAIN": "value",
        "QUOTED": "a value with spaces",
        "SINGLE": "another value",
        "EXPORTED": "exported-value",
        "SPACED": "padded",
        "COMMENTED": "value",
    }


def test_an_empty_value_is_a_value() -> None:
    """`AGENT_API_KEY=` means "explicitly empty", which is different from absent."""
    assert parse_env_file("AGENT_API_KEY=") == {"AGENT_API_KEY": ""}


def test_a_hash_inside_a_quoted_value_is_not_a_comment() -> None:
    """A key or URL may contain '#'. Truncating a secret produces a confusing 401."""
    assert parse_env_file('KEY_WITH_HASH="abc#def"') == {"KEY_WITH_HASH": "abc#def"}


def test_a_line_without_an_equals_sign_is_skipped() -> None:
    assert parse_env_file("nonsense\nPLAIN=value") == {"PLAIN": "value"}


def test_a_line_with_an_empty_key_is_skipped() -> None:
    assert parse_env_file("=value\nPLAIN=ok") == {"PLAIN": "ok"}


def test_no_interpolation_and_no_evaluation() -> None:
    """A config file that can run code is a different kind of thing from a config file."""
    parsed = parse_env_file("PLAIN=$HOME\nQUOTED=$(whoami)")
    assert parsed == {"PLAIN": "$HOME", "QUOTED": "$(whoami)"}


# -------------------------------------------------------------------- loading


def test_a_missing_file_is_not_an_error(tmp_path: Path, env: pytest.MonkeyPatch) -> None:
    """Most runs have no .env at all, and that must stay a non-event."""
    assert load_env_file(tmp_path / "absent") == 0


def test_values_are_loaded_into_the_environment(tmp_path: Path, env: pytest.MonkeyPatch) -> None:
    import os

    path = write_env(tmp_path, "PLAIN=loaded\nQUOTED='also loaded'\n")
    assert load_env_file(path) == 2
    assert os.environ["PLAIN"] == "loaded"
    assert os.environ["QUOTED"] == "also loaded"


def test_a_real_environment_variable_wins(tmp_path: Path, env: pytest.MonkeyPatch) -> None:
    """Twelve-factor precedence, and it is what makes a shell export a usable override."""
    import os

    env.setenv("ALREADY", "from-the-shell")
    path = write_env(tmp_path, "ALREADY=from-the-file\nPLAIN=from-the-file\n")
    assert load_env_file(path) == 1
    assert os.environ["ALREADY"] == "from-the-shell"
    assert os.environ["PLAIN"] == "from-the-file"


def test_loading_twice_is_harmless(tmp_path: Path, env: pytest.MonkeyPatch) -> None:
    path = write_env(tmp_path, "PLAIN=once\n")
    assert load_env_file(path) == 1
    assert load_env_file(path) == 0


def test_the_default_path_is_the_working_directory(tmp_path: Path, env: pytest.MonkeyPatch) -> None:
    assert Path(".env") == ENV_FILE
    path = write_env(tmp_path, "PLAIN=here\n")
    assert load_env_file(path) == 1


# ------------------------------------------- the property that was actually broken


def test_a_key_in_a_dotenv_file_reaches_the_provider(
    tmp_path: Path, env: pytest.MonkeyPatch
) -> None:
    """The whole point: this returned None before, and nothing said why."""
    config = load_config(REPO_ROOT / "configs" / "openai_compat.yaml")
    assert config.provider.api_key_env == "AGENT_API_KEY"
    assert read_api_key(config.provider) is None

    load_env_file(write_env(tmp_path, "AGENT_API_KEY=sk-from-the-dotenv-file\n"))
    assert read_api_key(config.provider) == "sk-from-the-dotenv-file"


# --------------------------------------------------------------- the drift guard


def entry_points() -> list[Path]:
    """Every Python file with a ``__main__`` block, which is every way in."""
    found = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        parts = set(path.parts)
        if parts & {".venv", ".pytest-tmp", "__pycache__"}:
            continue
        if '__name__ == "__main__"' in path.read_text(encoding="utf-8"):
            found.append(path)
    return found


def test_there_are_entry_points_to_check() -> None:
    """A guard on the guard: if the search stops finding them, it proves nothing."""
    assert len(entry_points()) >= 5


@pytest.mark.parametrize("path", entry_points(), ids=lambda path: path.name)
def test_every_entry_point_loads_the_env_file(path: Path) -> None:
    """A new script that forgets this reintroduces the silent failure, and only here."""
    assert "load_env_file()" in path.read_text(encoding="utf-8"), (
        f"{path.relative_to(REPO_ROOT)} has a __main__ block but never calls load_env_file(), "
        f"so a key placed in .env would be silently ignored when it is run"
    )


# ------------------------------------------------ the drift guard on the template


def advertised_variables() -> set[str]:
    """Names ``.env.example`` offers, whether commented out or not."""
    names = set()
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lstrip("#").strip()
        key, separator, _ = stripped.partition("=")
        if separator and key and key.replace("_", "").isalpha() and key.isupper():
            names.add(key)
    return names


def variables_the_code_reads() -> set[str]:
    """Names the code actually looks up, from ``os.environ`` or a provider's key field."""
    import re

    pattern = re.compile(r'environ(?:\.get\(|\[)\s*"([A-Z_][A-Z0-9_]*)"|api_key_env:\s*([A-Z_]+)')
    found: set[str] = set()
    for path in sorted(REPO_ROOT.rglob("*")):
        if path.suffix not in {".py", ".yaml"} or set(path.parts) & {".venv", ".pytest-tmp"}:
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            found.add(match.group(1) or match.group(2))
    return found


def test_the_template_advertises_something() -> None:
    assert advertised_variables(), "the search found nothing, so it proves nothing"


def test_the_template_only_advertises_variables_the_code_reads() -> None:
    """The defect that prompted this file, guarded.

    ``.env.example`` listed six variables nothing read — AGENT_BASE_URL, AGENT_MODEL,
    AGENT_PRICE_INPUT_PER_MTOK, AGENT_PRICE_OUTPUT_PER_MTOK, AGENT_PROVIDER and
    AGENT_TRACE_DIR — so setting them did nothing, silently. A template that offers a name
    the code ignores is worse than no template, because it answers the question wrongly.
    """
    unread = advertised_variables() - variables_the_code_reads()
    assert not unread, (
        f".env.example advertises variables nothing reads: {sorted(unread)}. Either wire "
        f"them up, or remove them — a name in this file is a promise that setting it does "
        f"something."
    )


def test_the_template_is_kept_out_of_version_control_when_copied() -> None:
    """`.env` holds a secret; the template does not, and they must stay distinguishable."""
    import subprocess

    result = subprocess.run(
        ["git", "check-ignore", ".env"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, ".env is not gitignored"
