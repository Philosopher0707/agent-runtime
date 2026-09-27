"""The built-in tools. One tool per class, each with its schema and side-effect class.

These are examples, not a framework: a new capability is a new file here plus a name
in a configuration.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tools.registry import Tool, ToolError

# ------------------------------------------------------------------------------- echo


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="Text to return unchanged.")


class EchoTool(Tool):
    name = "echo"
    description = "Return the given text unchanged. Useful for testing the tool path."
    args_model = EchoArgs
    optional = True
    timeout_s = 2.0

    def invoke(self, args: BaseModel) -> str:
        assert isinstance(args, EchoArgs)
        return args.text


# ------------------------------------------------------------------------- calculator

_ALLOWED_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

#: Guard rails on the evaluator: no unbounded expression, no absurd exponent.
_MAX_EXPRESSION_CHARS = 200
_MAX_EXPONENT = 64


class CalculatorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(description="An arithmetic expression, e.g. '21 * 2'.")


class CalculatorTool(Tool):
    """Arithmetic over a whitelisted AST. Never ``eval``.

    ``eval`` on model-supplied text is the classic way a tool boundary becomes an
    execution boundary. This walks a parsed tree and rejects every node it did not
    explicitly allow.
    """

    name = "calculator"
    description = "Evaluate an arithmetic expression. Supports + - * / // % ** and parentheses."
    args_model = CalculatorArgs
    optional = True
    timeout_s = 2.0

    def invoke(self, args: BaseModel) -> str:
        assert isinstance(args, CalculatorArgs)
        if len(args.expression) > _MAX_EXPRESSION_CHARS:
            raise ToolError(f"expression longer than {_MAX_EXPRESSION_CHARS} characters")
        try:
            tree = ast.parse(args.expression, mode="eval")
        except SyntaxError as exc:
            raise ToolError(f"not an expression: {exc.msg}") from exc
        value = _evaluate(tree.body)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)


def _evaluate(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ToolError(f"unsupported literal: {node.value!r}")
        return node.value
    if isinstance(node, ast.BinOp):
        handler = _ALLOWED_BINARY.get(type(node.op))
        if handler is None:
            raise ToolError(f"unsupported operator: {type(node.op).__name__}")
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ToolError(f"exponent above {_MAX_EXPONENT} is refused")
        try:
            return handler(left, right)
        except ZeroDivisionError as exc:
            raise ToolError("division by zero") from exc
    if isinstance(node, ast.UnaryOp):
        handler = _ALLOWED_UNARY.get(type(node.op))
        if handler is None:
            raise ToolError(f"unsupported unary operator: {type(node.op).__name__}")
        return handler(_evaluate(node.operand))
    raise ToolError(f"unsupported syntax: {type(node).__name__}")


# -------------------------------------------------------------------------------- clock


class ClockArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: Literal["iso", "epoch"] = Field(default="iso", description="Output format.")


class ClockTool(Tool):
    name = "clock"
    description = "Return the current UTC time."
    args_model = ClockArgs
    optional = True
    timeout_s = 2.0

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def invoke(self, args: BaseModel) -> str:
        assert isinstance(args, ClockArgs)
        moment = self._now()
        return str(int(moment.timestamp())) if args.style == "epoch" else moment.isoformat()


# --------------------------------------------------------------------------- write_note


class WriteNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(description="File name under the notes directory, e.g. 'todo.txt'.")
    text: str = Field(description="Contents to write.")
    #: Not advertised to the model (see tools.registry._public_schema) and overwritten
    #: by dispatch with the caller's token. Declared required so registration demands it.
    confirmation_token: str = Field(description="Supplied by the caller. Never by the model.")


class WriteNoteTool(Tool):
    """The worked example of a side-effecting tool.

    It changes state, so it is not idempotent, so it is never retried — and it cannot
    be invoked at all without a confirmation token from the principal.
    """

    name = "write_note"
    description = "Write a note to a file in the notes directory. Changes state."
    args_model = WriteNoteArgs
    side_effect = True
    idempotent = False
    optional = False
    timeout_s = 5.0

    def __init__(self, *, root: str | Path = ".notes") -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def invoke(self, args: BaseModel) -> str:
        assert isinstance(args, WriteNoteArgs)
        target = self._resolve(args.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args.text, encoding="utf-8")
        return f"wrote {len(args.text)} characters to {target}"

    def _resolve(self, filename: str) -> Path:
        """Resolve inside the notes directory, or refuse. No traversal, no absolute paths."""
        if not filename.strip():
            raise ToolError("filename is empty")
        candidate = Path(filename)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ToolError(f"filename must be relative and must not traverse: {filename!r}")
        root = self._root.resolve()
        target = (root / candidate).resolve()
        if not target.is_relative_to(root):
            raise ToolError(f"filename escapes the notes directory: {filename!r}")
        return target


# ---------------------------------------------------------------------- ask_clarification


class AskClarificationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(description="One question that resolves the ambiguity.")


class AskClarificationTool(Tool):
    """A *control* tool. The loop intercepts it; it is never dispatched.

    It exists so that "the model wants to ask something" is a first-class, countable
    event rather than a sentence we pattern-match out of prose.
    """

    name = "ask_clarification"
    description = "Ask the caller exactly one question when the task is genuinely ambiguous."
    args_model = AskClarificationArgs
    optional = True
    timeout_s = 1.0

    def invoke(self, args: BaseModel) -> str:  # pragma: no cover - never dispatched
        raise ToolError("ask_clarification is a control tool and must be intercepted by the loop")


#: Tools the runtime itself needs. Registered for every configuration, whether or not
#: a config names them, because they are protocol rather than capability.
CONTROL_TOOL_NAMES: tuple[str, ...] = (AskClarificationTool.name,)

#: The one tool name the loop knows. It is intercepted before dispatch, never called.
#: Exported as a name rather than a class so the loop depends on the protocol, not on
#: an implementation.
CLARIFICATION_TOOL_NAME: str = AskClarificationTool.name

BUILTIN_TOOLS: dict[str, Callable[..., Tool]] = {
    EchoTool.name: EchoTool,
    CalculatorTool.name: CalculatorTool,
    ClockTool.name: ClockTool,
    WriteNoteTool.name: WriteNoteTool,
    AskClarificationTool.name: AskClarificationTool,
}


__all__ = [
    "BUILTIN_TOOLS",
    "CLARIFICATION_TOOL_NAME",
    "CONTROL_TOOL_NAMES",
    "AskClarificationArgs",
    "AskClarificationTool",
    "CalculatorArgs",
    "CalculatorTool",
    "ClockArgs",
    "ClockTool",
    "EchoArgs",
    "EchoTool",
    "WriteNoteArgs",
    "WriteNoteTool",
]
