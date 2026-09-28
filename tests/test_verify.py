"""The verify domain's tools, and the boundary they draw.

The tools are thin; what is worth testing is the boundary. A source name arrives from the model,
so it is a name and never a path — the same rule the triage domain follows, for the same reason.

The four verdicts are not tested here. They are the *model's* judgement, and a stub cannot make
one — they are pinned by the live cases and the prompt, not by a unit test. What a unit test can
pin is that the tool set offers no way to record anything, which is a design choice rather than an
omission.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import load_config_by_name
from tools.catalogue import available_tool_names
from tools.registry import ToolError, ToolRegistry
from tools.verify import (
    ListSourcesArgs,
    ListSourcesTool,
    ReadSourceArgs,
    ReadSourceTool,
    SourceStore,
)

SOURCES = Path(__file__).resolve().parent.parent / "sources"


@pytest.fixture
def store(tmp_path: Path) -> SourceStore:
    (tmp_path / "one.md").write_text("# One\n\nA document.\n", encoding="utf-8")
    return SourceStore(tmp_path)


# ------------------------------------------------------------------ the boundary


@pytest.mark.parametrize("bad", ["", "   ", "../secrets.md", "/etc/passwd", "a/b.md", "a\\b"])
def test_a_source_name_is_a_name_and_never_a_path(store: SourceStore, bad: str) -> None:
    with pytest.raises(ToolError):
        store.read(bad)


def test_a_plain_name_is_accepted(store: SourceStore) -> None:
    assert "A document." in store.read("one.md")


def test_an_unknown_source_says_what_there_is(store: SourceStore) -> None:
    """A refusal that names the alternatives is a refusal the model can act on."""
    with pytest.raises(ToolError, match=r"have: one\.md"):
        store.read("two.md")


def test_an_empty_directory_is_not_an_error(tmp_path: Path) -> None:
    assert SourceStore(tmp_path).names() == []


# --------------------------------------------------------------------- the tools


def test_list_sources_names_what_is_available() -> None:
    listed = ListSourcesTool(root=SOURCES).invoke(ListSourcesArgs())
    assert "northwind-sync.md" in listed
    assert "northwind-support.md" in listed


def test_read_source_returns_the_document() -> None:
    read = ReadSourceTool(root=SOURCES).invoke(ReadSourceArgs(name="northwind-sync.md"))
    assert "99.9%" in read


def test_the_source_fixtures_cover_all_four_verdicts() -> None:
    """A corpus that cannot exercise a verdict cannot pin it.

    The same rule the marker corpus learned: a rule needs a *sample* covering it, or the metric
    will not notice its removal. `supported`, `contradicted`, `absent` and `undecidable` each need
    something in the fixtures that produces them.
    """
    text = "\n".join(
        ReadSourceTool(root=SOURCES).invoke(ReadSourceArgs(name=name))
        for name in ("northwind-sync.md", "northwind-releases.md", "northwind-support.md")
    )
    assert "99.9%" in text, "nothing supports a claim"
    assert "30 days" in text and "down from 90" in text, "nothing contradicts a claim"
    assert "still under discussion" in text, "nothing is undecidable"
    assert "free tier" not in text, "nothing is absent"


def test_a_source_may_contain_an_instruction_and_is_still_only_data() -> None:
    """The injection is a fixture, so the prompt is not the only thing standing between it and
    the model. `northwind-support.md` tells the reader to mark everything supported; the tool
    returns it verbatim, and the envelope — not this module — is what says it is data."""
    read = ReadSourceTool(root=SOURCES).invoke(ReadSourceArgs(name="northwind-support.md"))
    assert "previous instructions" in read
    assert "draft" in read


# ------------------------------------------------- no way to record, on purpose


def test_the_domain_offers_no_side_effect() -> None:
    """This agent reports; it does not act. A side effect would be a second thing to get right,
    and the first version of a capability should have one job."""
    registry = ToolRegistry([ListSourcesTool(root=SOURCES), ReadSourceTool(root=SOURCES)])
    try:
        assert not any(d.side_effect for d in registry.descriptors())
    finally:
        registry.close()


def test_the_configuration_loads_and_names_only_real_tools() -> None:
    config = load_config_by_name("verify", root="configs")
    assert set(config.tools) <= set(available_tool_names())
    assert set(config.tools) == {"list_sources", "read_source"}


def test_the_configuration_declares_where_its_sources_are() -> None:
    """Rather than relying on the tool's default — the point of decisions/0027."""
    config = load_config_by_name("verify", root="configs")
    assert set(config.tool_options) == {"list_sources", "read_source"}
    assert all(options["root"] == "sources" for options in config.tool_options.values())


def test_the_output_schema_makes_all_four_verdicts_expressible() -> None:
    """If the schema allowed only `supported` and `contradicted`, the prompt's care about
    `absent` and `undecidable` would be unreachable — the agent could not say what it found."""
    config = load_config_by_name("verify", root="configs")
    schema = config.output.schema_
    verdicts = schema["properties"]["findings"]["items"]["properties"]["verdict"]["enum"]
    assert set(verdicts) == {"supported", "contradicted", "absent", "undecidable"}
