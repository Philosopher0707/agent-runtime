"""Repo hygiene: the files that instruct future agents must stay usable.

`AGENTS.md` says its loader "cuts the tail" past 8,000 bytes. Silent truncation of
instructions is the failure mode — an agent would read a spec that stops mid-sentence and
have no way to know. The budget is therefore checked rather than trusted.

The same reasoning applies to `AGENTS_LEARNING.md`: an obligation to maintain a file is
only durable if it is discoverable from the file every agent reads first, and if the file's
own format is enforced rather than remembered.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"
README_MD = REPO_ROOT / "README.md"
REFERENCE_MD = REPO_ROOT / "REFERENCE.md"
LEARNING_LOG = REPO_ROOT / "AGENTS_LEARNING.md"

#: The budget AGENTS.md states for itself.
SIZE_BUDGET_BYTES = 8_000

DATE_HEADING = re.compile(r"^### \d{4}-\d{2}-\d{2}\b", re.MULTILINE)


def test_agents_md_stays_within_its_own_size_budget() -> None:
    size = len(AGENTS_MD.read_bytes())
    assert size <= SIZE_BUDGET_BYTES, (
        f"AGENTS.md is {size} bytes, over its own {SIZE_BUDGET_BYTES}-byte budget by "
        f"{size - SIZE_BUDGET_BYTES}. The loader cuts the tail, so the end of the file is "
        f"now silently missing. Move detail into docs/ and leave a pointer."
    )


def test_agents_md_points_at_the_learning_log() -> None:
    """The obligation has to be discoverable from the file every agent reads first."""
    assert "AGENTS_LEARNING.md" in AGENTS_MD.read_text(encoding="utf-8"), (
        "AGENTS.md no longer points at AGENTS_LEARNING.md. An agent that never learns the "
        "log exists will never append to it."
    )


def test_readme_points_at_the_learning_log() -> None:
    assert "AGENTS_LEARNING.md" in README_MD.read_text(encoding="utf-8")


def test_the_learning_log_exists_and_declares_its_sections() -> None:
    text = LEARNING_LOG.read_text(encoding="utf-8")
    assert "## Open questions" in text
    assert "## Log" in text
    assert "## How to maintain this file" in text


def test_the_learning_log_has_entries() -> None:
    text = LEARNING_LOG.read_text(encoding="utf-8")
    assert DATE_HEADING.search(text), "the log has no dated entry"


def test_every_log_entry_is_dated() -> None:
    """Entries are `### YYYY-MM-DD — title`. An undated entry cannot be referenced, and
    the log is meant to be referenced (a later entry may supersede an earlier one)."""
    headings = re.findall(r"^### .+$", LEARNING_LOG.read_text(encoding="utf-8"), re.MULTILINE)
    assert headings, "the log has no entries"
    undated = [heading for heading in headings if not DATE_HEADING.match(heading)]
    assert not undated, f"log entries must start with a date: {undated}"


@pytest.mark.parametrize("path", [AGENTS_MD, LEARNING_LOG, README_MD])
def test_instruction_files_are_not_empty(path: Path) -> None:
    assert path.read_text(encoding="utf-8").strip()


# ------------------------------------------------------------------- the docs tree

LINK = re.compile(r"\]\(([^)#\s]+\.md)\)")


def markdown_files() -> list[Path]:
    return sorted([*REPO_ROOT.glob("*.md"), *(REPO_ROOT / "docs").rglob("*.md")])


def test_the_docs_tree_is_found() -> None:
    """A guard on the guard: if the search stops finding files, the link check proves nothing."""
    assert len(markdown_files()) >= 15


def test_internal_markdown_links_resolve() -> None:
    """A docs tree is only as good as its links, and a moved file orphans them silently.

    Cross-references are how the decision record stays navigable — `0011` points at `0006`,
    the architecture doc points at the decisions that justify it. A broken one is invisible
    until someone clicks it.
    """
    broken: list[str] = []
    for path in markdown_files():
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(REPO_ROOT)} -> {target}")
    assert not broken, f"broken internal doc links: {broken}"


def test_the_link_check_is_not_vacuous() -> None:
    """The floor is deliberately well below the current count.

    Its job is to catch the *pattern* drifting and silently matching nothing — not to pin a
    number that a doc edit would then fail. There are around 17 internal links today; the
    floor is 10.
    """
    total = sum(len(LINK.findall(path.read_text(encoding="utf-8"))) for path in markdown_files())
    assert total >= 10, (
        f"only {total} internal links found across {len(markdown_files())} docs — the pattern "
        f"has probably drifted and the link check is now vacuous"
    )


# ------------------------------------------------- references that have to stay true


#: `make <target>` in a code block or inline code. Deliberately not a bare `\bmake (\w+)`:
#: "the invariants that make replay exact" is prose, not a target. MULTILINE matters — without
#: it `^` only matches at the start of the file and the check finds almost nothing.
MAKE_TARGET = re.compile(r"^\s*make ([a-z][a-z-]+)|`make ([a-z][a-z-]+)", re.MULTILINE)
MAKEFILE_TARGET = re.compile(r"^([a-z][a-z-]+):", re.MULTILINE)
DECISION_ID = re.compile(r"\b0\d{3}\b")
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")


def makefile_targets() -> set[str]:
    return set(MAKEFILE_TARGET.findall((REPO_ROOT / "Makefile").read_text(encoding="utf-8")))


def readme_make_targets() -> set[str]:
    found = MAKE_TARGET.findall(README_MD.read_text(encoding="utf-8"))
    return {first or second for first, second in found}


def test_the_readme_names_some_targets() -> None:
    assert len(readme_make_targets()) >= 5, "the pattern has drifted and now proves nothing"


def test_every_make_target_the_readme_names_exists() -> None:
    """A README documenting a target the Makefile does not have sends people to a failure.

    The README is the first thing anyone runs, so this is the most expensive place for a
    reference to rot.
    """
    missing = readme_make_targets() - makefile_targets()
    assert not missing, (
        f"the README names make targets that do not exist: {sorted(missing)}. Either add them "
        f"to the Makefile or fix the README."
    )


def test_every_decision_the_readme_cites_is_linked() -> None:
    """A bare `0002` is a reference nothing checks.

    The link check covers *links*. A backticked decision id is invisible to it, so a renamed
    or superseded decision can rot in the README silently — and the README is where a new
    reader starts. Linking the citation puts it under the check that already exists.
    """
    text = README_MD.read_text(encoding="utf-8")
    spans = [match.span() for match in MARKDOWN_LINK.finditer(text)]
    bare = [
        match.group()
        for match in DECISION_ID.finditer(text)
        if not any(start <= match.start() and match.end() <= end for start, end in spans)
    ]
    assert not bare, (
        f"the README cites {sorted(set(bare))} without linking — a bare id is unchecked. Use "
        f"[0002](docs/decisions/0002-....md)."
    )


# ----------------------------------------------------- citations of the test suite


#: `test_foo.py` — a file citation. `test_foo` not followed by `.py` — a function citation.
CITED_TEST_FILE = re.compile(r"\b(test_[a-z_0-9]+)\.py")
CITED_TEST_FUNC = re.compile(r"\b(test_[a-z_0-9]+)\b(?!\.py)")


def citing_docs() -> list[Path]:
    """The **reference** docs: the reference itself, README, the spec, and `docs/`.

    `AGENTS_LEARNING.md` is deliberately excluded. It is a historical record of mistakes, so it
    must be free to name things that were wrong — including a test that never existed, which is
    precisely what L70 does. Requiring every name in the log to resolve would make the log
    unable to report its own errors.

    The reference docs are the opposite: they make claims a reader acts on, so every citation
    in them must resolve. The false citation this check was written for appeared in *both* a
    decision record and the log, so excluding the log still catches it.

    `REFERENCE.md` is included because it is the densest citer of tests in the repository — every
    rule in it names the test that pins the rule. A file that cites tests as evidence is exactly
    what this check is for, and a root file would otherwise be the one place a citation could rot
    unnoticed. It is tracked, unlike `REGISTER.md` and `NOTES.md`, which are generated and whose
    contents are derived from the things they name.
    """
    return [REFERENCE_MD, README_MD, AGENTS_MD, *(REPO_ROOT / "docs").rglob("*.md")]


def suite_files() -> set[str]:
    """Named without a `test_` prefix on purpose.

    A helper called `test_*` is collected by pytest as a test — it runs, and it "passes" if it
    returns anything. These two did exactly that until the warnings gave them away. Same reason
    `tests/helpers.py` is not collected: a helper must not look like a test.
    """
    return {path.stem for path in (REPO_ROOT / "tests").glob("*.py")}


def suite_functions() -> set[str]:
    found: set[str] = set()
    for path in (REPO_ROOT / "tests").glob("*.py"):
        found |= set(re.findall(r"^def (test_[a-z_0-9]+)", path.read_text(encoding="utf-8"), re.M))
    return found


def test_the_suite_is_found() -> None:
    """A guard on the guard."""
    assert len(suite_files()) >= 15
    assert len(suite_functions()) >= 100


def test_every_test_file_the_docs_cite_exists() -> None:
    cited: set[str] = set()
    for path in citing_docs():
        cited |= set(CITED_TEST_FILE.findall(path.read_text(encoding="utf-8")))
    assert not cited - suite_files(), (
        f"docs cite test files that do not exist: {sorted(cited - suite_files())}"
    )


def test_every_test_function_the_docs_cite_exists() -> None:
    """The docs cite tests as evidence. A citation of a test that does not exist is worse
    than no citation: it reads as proof and there is nothing behind it.

    This found one on its first run — `test_task_text_is_not_scanned_as_injection`, cited in
    decision 0009 and in the learning log as the executable proof of the trust model, and
    never written. The behaviour was covered the whole time, by an eval case. The citation
    was wrong, not the claim.
    """
    cited: set[str] = set()
    for path in citing_docs():
        cited |= set(CITED_TEST_FUNC.findall(path.read_text(encoding="utf-8")))
    missing = sorted(cited - suite_functions())
    assert not missing, (
        f"docs cite test functions that do not exist: {missing}. A cited test reads as "
        f"evidence — either write it, or cite the artefact that actually covers it."
    )


def test_the_citation_checks_are_not_vacuous() -> None:
    files_cited = funcs_cited = 0
    for path in citing_docs():
        text = path.read_text(encoding="utf-8")
        files_cited += len(CITED_TEST_FILE.findall(text))
        funcs_cited += len(CITED_TEST_FUNC.findall(text))
    assert files_cited >= 10, f"only {files_cited} test-file citations found"
    assert funcs_cited >= 5, f"only {funcs_cited} test-function citations found"


# --------------------------------------------------------- naming the tool set


BACKTICKED_IDENT = re.compile(r"`([a-z][a-z_0-9]+)`")


def test_no_doc_names_a_tool_that_does_not_exist() -> None:
    """A doc listing the tool set is making a claim, and one of them was wrong.

    The roadmap listed `fetch` among the tools for the whole life of the project. There has
    never been a `fetch`. Nothing checked, because a tool name is a plain lowercase word in
    prose — indistinguishable from any other identifier.

    It becomes distinguishable in the one place it matters: a line that names **two or more
    real tools** is a line *about* the tool set, so every backticked identifier on it must be
    a tool. That rule flags exactly this defect and nothing else across every doc.
    """
    from tools.catalogue import available_tool_names

    tools = set(available_tool_names())
    offenders: list[str] = []
    for path in citing_docs():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            names = set(BACKTICKED_IDENT.findall(line))
            if len(names & tools) >= 2 and (names - tools):
                offenders.append(f"{path.name}:{number} names {sorted(names - tools)}")
    assert not offenders, (
        f"docs name tools that do not exist: {offenders}. Available: {sorted(tools)}"
    )


def test_the_tool_name_check_is_not_vacuous() -> None:
    """It must find lines that *are* about the tool set, or it proves nothing."""
    from tools.catalogue import available_tool_names

    tools = set(available_tool_names())
    about_tools = 0
    for path in citing_docs():
        for line in path.read_text(encoding="utf-8").splitlines():
            if len(set(BACKTICKED_IDENT.findall(line)) & tools) >= 2:
                about_tools += 1
    assert about_tools >= 1, "no doc line names two real tools — the check is vacuous"


# ------------------------------------------------ the one rule, made checkable


#: A bare string literal that could be a tool name.
STRING_LITERAL = re.compile(r"[\"']([a-z][a-z_0-9]+)[\"']")


def string_literals_in_code(source: str) -> list[str]:
    """Every string literal in **code**, excluding docstrings.

    AST rather than text, for the reason the tripwire catalogue gives first: *AST over text,
    always*. A text search reads the prose that **describes** the problem — and the first
    version of this check flagged `config.py` and `factory.py` for naming `write_note` in the
    docstrings that explain why they no longer name it in code.

    Comments are not in the AST at all, so they are excluded for free.
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        body = getattr(node, "body", [])
        if not isinstance(node, holders) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def domain_tool_names() -> set[str]:
    """Every registered tool that is not a control tool.

    Control tools are protocol — the loop is allowed to know `ask_clarification` by name,
    because intercepting it *is* the loop's job. Everything else is a capability, and a
    capability the core names is a capability the core has an opinion about.
    """
    from tools.builtin import CONTROL_TOOL_NAMES
    from tools.catalogue import available_tool_names

    return set(available_tool_names()) - set(CONTROL_TOOL_NAMES)


def test_the_core_names_no_capability() -> None:
    """The one rule, enforced rather than asserted.

    "The core loop contains no domain logic" is the claim every other decision rests on — it is
    why adding a capability is a configuration rather than a branch. It was **true and measured**
    (adding the first real domain touched one line, in the documented seam) and it was **not
    enforced**: a tool name leaking into the core would have passed CI.

    This check covered only `runtime/loop.py` when it was written, because `runtime/factory.py`
    named `write_note` to pass it a constructor argument — the one place the core knew a tool.
    That is fixed ([decisions/0027](../../docs/decisions/0027-tool-options-are-configuration.md)),
    so the check now covers the whole core, which is what the rule actually says.

    Control tools are excluded on purpose: intercepting `ask_clarification` *is* the loop's job,
    which is why the rule says "exactly one tool name".
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "runtime").glob("*.py")):
        literals = set(string_literals_in_code(path.read_text(encoding="utf-8")))
        for name in sorted(domain_tool_names() & literals):
            offenders.append(f"{path.name} names {name!r}")
    assert not offenders, (
        f"{offenders}. A capability the core names is a capability the core has an opinion "
        f"about — it belongs in a configuration, a tool, or the model adapter."
    )


def test_that_check_is_not_vacuous() -> None:
    """It must be looking for something real, across real files."""
    assert len(domain_tool_names()) >= 4, "no domain tools registered — nothing to leak"
    core = sorted((REPO_ROOT / "runtime").glob("*.py"))
    assert len(core) >= 5, "the check is reading almost no files"
    assert any(string_literals_in_code(p.read_text(encoding="utf-8")) for p in core)


def test_the_literal_reader_ignores_prose() -> None:
    """A docstring that names a tool is not the core naming a tool.

    The first version of this check was text-based and flagged exactly that, which is the
    mistake the tripwire catalogue warns about in its first rule.
    """
    source = '"""A docstring mentioning write_note."""\nX = "write_note"\n# a comment: write_note\n'
    assert string_literals_in_code(source) == ["write_note"]


# ------------------------------- the "delete the capability" test, made checkable


#: What `runtime/` may import from `tools/`. These three are the *seam*: resolving a name to a
#: factory, enforcing what a tool must be, and listing what exists. Everything else under
#: `tools/` is a capability — or a test double — and the core must not know it exists.
TOOL_SEAM = frozenset({"tools.catalogue", "tools.registry", "tools.builtin"})


def core_tool_imports() -> list[tuple[str, str]]:
    """Every `tools.*` module the core imports, by file."""
    found: list[tuple[str, str]] = []
    for path in sorted((REPO_ROOT / "runtime").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                module = next((a.name for a in node.names if a.name.startswith("tools")), None)
            if module and module.split(".")[0] == "tools":
                found.append((path.name, module))
    return found


def test_the_core_imports_no_capability() -> None:
    """The "delete the capability" test, enforced.

    The test the project has been applying by hand: *can the capability be deleted and the
    runtime still work?* It is the invariant that keeps a growing project from turning its
    runtime into a framework for one kind of agent — and it is the thing most likely to break
    quietly, because adding a capability is exactly when the core is most tempted to learn about
    one.

    Measured, not asserted: adding the triage domain touched eighteen new files and one line in
    the documented seam. This makes the measurement a tripwire.

    Note what it does *not* forbid. `tools/builtin.py` imports `tools/triage` — the capability is
    named at the seam, which is where a capability is supposed to be named. What it forbids is
    the *core* reaching past the seam into a capability's module.
    """
    offenders = [
        f"{where} imports {module}"
        for where, module in core_tool_imports()
        if module not in TOOL_SEAM
    ]
    assert not offenders, (
        f"{offenders}. The core may import the seam ({sorted(TOOL_SEAM)}) and nothing else "
        f"under tools/. If the core needs a capability, the capability belongs in the seam or "
        f"the need belongs in a configuration."
    )


def test_the_seam_check_is_not_vacuous() -> None:
    """It must be reading real imports, and the seam must be the whole of what it allows."""
    imports = core_tool_imports()
    assert imports, "the check found no tools imports at all — it is reading nothing"
    assert {module for _, module in imports} <= TOOL_SEAM, "the core already imports past the seam"


# ------------------------------- every entry point wires the same facilities


def build_tools_call_sites() -> list[tuple[Path, int, str]]:
    """Every call to `build_tools`, outside the runtime that defines it.

    Read from the AST rather than the text, for the reason the catalogue's first rule gives — and
    because a call spanning several lines is exactly what a text search gets wrong.
    """
    sites: list[tuple[Path, int, str]] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if ".venv" in path.parts or path.parent.name == "runtime":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "build_tools":
                sites.append((path, node.lineno, path.read_text(encoding="utf-8")))
    return sites


def test_every_entry_point_that_runs_a_task_supplies_the_facilities() -> None:
    """A facility wired in one entry point and not another is a tool that works from the CLI and
    fails from the harness.

    That is not hypothetical: the first live sweep came back `degraded` because `spawn_agent` had
    no runner. `run_task` wired one; the live harness, the golden harness and the test helper did
    not.

    **Scoped to files that actually run a task.** Building a registry to *inspect* it — counting
    the tools, checking a config loads — needs no facilities, and demanding them there would be
    noise that teaches people to add `facilities=None` to make a check go quiet.
    """
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if ".venv" in path.parts or path.parent.name == "runtime":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        if not any(_is_named_call(node, "run") for node in ast.walk(tree)):
            continue
        for node in ast.walk(tree):
            if _is_named_call(node, "build_tools") and "facilities=" not in _call_text(
                path, node.lineno
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        f"these build_tools call sites run a task but pass no facilities: {offenders}. Use "
        f"run_facilities() — a tool that needs a runtime facility must work the same everywhere."
    )


def _is_named_call(node: ast.AST, name: str) -> bool:
    """A call to a **bare name**, not an attribute.

    `from runtime.loop import run` is called as `run(...)`; `uvicorn.run(...)` is not the same
    function and is not a run of a task. Matching attributes made the first version of this check
    flag `service/app.py` for starting a web server.
    """
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def _call_text(path: Path, lineno: int) -> str:
    """The source of the call starting at ``lineno``, up to the next dedent."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lineno - 1
    indent = len(lines[start]) - len(lines[start].lstrip())
    collected = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        collected.append(line)
    return "\n".join(collected)


def test_the_facility_check_is_not_vacuous() -> None:
    """It must be finding files that run tasks, and sites that wire facilities."""
    runners = [
        path
        for path in REPO_ROOT.rglob("*.py")
        if ".venv" not in path.parts
        and path.parent.name != "runtime"
        and any(_is_named_call(node, "run") for node in ast.walk(ast.parse(path.read_text())))
    ]
    assert len(runners) >= 3, f"only {len(runners)} files call run() — is the check reading?"
    wired = [path for path in runners if "facilities=" in path.read_text(encoding="utf-8")]
    assert wired, "no file that runs a task wires facilities — the check proves nothing"
