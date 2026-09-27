"""The structured-output validator: what it checks, and what it admits it does not.

The second half matters as much as the first. A validator that silently ignores keywords
it does not understand is worse than one that refuses them, because the caller believes
a guarantee that was never made.
"""

from __future__ import annotations

import pytest

from runtime.structured import SUPPORTED_KEYWORDS, parse_structured, validate

OBJECT_SCHEMA = {
    "type": "object",
    "required": ["total", "currency"],
    "additionalProperties": False,
    "properties": {
        "total": {"type": "integer"},
        "currency": {"type": "string", "enum": ["CNY", "USD"]},
    },
}


def test_a_conforming_document_parses() -> None:
    result = parse_structured('{"total": 42, "currency": "CNY"}', OBJECT_SCHEMA)
    assert result.ok
    assert result.value == {"total": 42, "currency": "CNY"}


def test_a_fenced_document_is_unwrapped() -> None:
    result = parse_structured('```json\n{"total": 1, "currency": "USD"}\n```', OBJECT_SCHEMA)
    assert result.ok


def test_invalid_json_is_reported_with_a_position() -> None:
    result = parse_structured('{"total": }', OBJECT_SCHEMA)
    assert not result.ok
    assert "invalid JSON at line" in (result.error or "")


def test_an_empty_answer_is_rejected() -> None:
    assert not parse_structured("   ", OBJECT_SCHEMA).ok


def test_a_missing_required_property_is_named() -> None:
    result = parse_structured('{"total": 1}', OBJECT_SCHEMA)
    assert not result.ok
    assert "missing required property 'currency'" in (result.error or "")


def test_a_wrong_type_is_named_with_its_path() -> None:
    result = parse_structured('{"total": "one", "currency": "CNY"}', OBJECT_SCHEMA)
    assert not result.ok
    assert "$.total" in (result.error or "")
    assert "expected integer" in (result.error or "")


def test_an_enum_violation_is_named() -> None:
    result = parse_structured('{"total": 1, "currency": "GBP"}', OBJECT_SCHEMA)
    assert not result.ok
    assert "is not one of" in (result.error or "")


def test_additional_properties_are_refused_when_the_schema_says_so() -> None:
    result = parse_structured('{"total": 1, "currency": "CNY", "extra": true}', OBJECT_SCHEMA)
    assert not result.ok
    assert "unexpected properties" in (result.error or "")


def test_a_boolean_is_not_an_integer() -> None:
    """JSON is explicit about this; Python's bool-is-an-int would get it wrong."""
    assert validate(True, {"type": "integer"}) is not None
    assert validate(True, {"type": "boolean"}) is None
    assert validate(1, {"type": "number"}) is None


def test_arrays_are_validated_element_wise() -> None:
    schema = {"type": "array", "items": {"type": "integer"}}
    assert validate([1, 2, 3], schema) is None
    error = validate([1, "two"], schema)
    assert error is not None
    assert "$[1]" in error


def test_a_union_type_is_accepted() -> None:
    assert validate(None, {"type": ["string", "null"]}) is None
    assert validate(5, {"type": ["string", "null"]}) is not None


def test_no_schema_means_any_valid_json() -> None:
    assert parse_structured("[1, 2]", None).ok
    assert parse_structured('"a string"', None).ok


def test_unsupported_keywords_are_declared_not_silently_ignored() -> None:
    """The honest limitation, asserted so it cannot be forgotten."""
    assert "allOf" not in SUPPORTED_KEYWORDS
    assert "$ref" not in SUPPORTED_KEYWORDS
    assert {
        "type",
        "required",
        "properties",
        "items",
        "enum",
        "additionalProperties",
    } == SUPPORTED_KEYWORDS


@pytest.mark.parametrize("keyword", sorted(SUPPORTED_KEYWORDS))
def test_every_advertised_keyword_is_actually_enforced(keyword: str) -> None:
    """The other half of the honesty claim: what is advertised is implemented."""
    cases: dict[str, tuple[object, dict[str, object], bool]] = {
        "type": (1, {"type": "string"}, False),
        "required": ({}, {"type": "object", "required": ["a"]}, False),
        "properties": (
            {"a": "x"},
            {"type": "object", "properties": {"a": {"type": "integer"}}},
            False,
        ),
        "items": (["x"], {"type": "array", "items": {"type": "integer"}}, False),
        "enum": ("z", {"enum": ["a", "b"]}, False),
        "additionalProperties": (
            {"a": 1},
            {"type": "object", "properties": {}, "additionalProperties": False},
            False,
        ),
    }
    value, schema, expected = cases[keyword]
    assert (validate(value, schema) is None) is expected
