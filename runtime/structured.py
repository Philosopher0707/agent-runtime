"""Structured output: a JSON Schema *subset* validator.

Deliberately not a full JSON Schema implementation. It covers the keywords the
runtime actually enforces — ``type``, ``required``, ``properties``, ``items``,
``enum``, ``additionalProperties`` — and says nothing about the rest rather than
pretending to check them. The upgrade path is a real library; the trigger is the
first schema that needs ``$ref`` or ``allOf``. See docs/decisions/0008.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The keywords this validator claims to enforce. Anything else is ignored, and that
#: is a documented limitation rather than a silent one.
SUPPORTED_KEYWORDS = frozenset(
    {"type", "required", "properties", "items", "enum", "additionalProperties"}
)


@dataclass(frozen=True)
class StructuredResult:
    ok: bool
    value: Any = None
    error: str | None = None


def parse_structured(text: str, schema: dict[str, Any] | None) -> StructuredResult:
    """Parse ``text`` as JSON and, if a schema is given, validate against it."""
    stripped = _strip_fence(text.strip())
    if not stripped:
        return StructuredResult(False, error="the answer was empty")
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return StructuredResult(
            False,
            error=f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}",
        )
    if schema is not None:
        error = validate(value, schema)
        if error is not None:
            return StructuredResult(False, error=error)
    return StructuredResult(True, value=value)


def validate(value: Any, schema: dict[str, Any]) -> str | None:
    """Return a path-qualified error message, or None if the value conforms."""
    return _validate(value, schema, path="$")


def _validate(value: Any, schema: Any, *, path: str) -> str | None:
    if not isinstance(schema, dict):
        return None

    if "type" in schema:
        expected = schema["type"]
        options = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, option) for option in options):
            return f"{path}: expected {expected}, got {_typename(value)}"

    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: {value!r} is not one of {schema['enum']}"

    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                return f"{path}: missing required property {key!r}"
        for key, subschema in (schema.get("properties") or {}).items():
            if key in value:
                error = _validate(value[key], subschema, path=f"{path}.{key}")
                if error is not None:
                    return error
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties") or {})
            extra = sorted(set(value) - allowed)
            if extra:
                return f"{path}: unexpected properties {extra}"

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            error = _validate(item, schema["items"], path=f"{path}[{index}]")
            if error is not None:
                return error

    return None


def _matches_type(value: Any, expected: Any) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        # bool is a subclass of int; JSON says they are different things.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def _typename(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _strip_fence(text: str) -> str:
    """Unwrap a ```json ... ``` fence if the whole answer is one."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2:
        return text
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


__all__ = ["SUPPORTED_KEYWORDS", "StructuredResult", "parse_structured", "validate"]
