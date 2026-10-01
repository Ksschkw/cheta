"""Safe arithmetic.

``eval`` is never used. The expression is parsed with ``ast`` and evaluated by
walking a whitelist: numbers, parentheses, and a fixed set of operators. Names,
calls, attributes, subscriptions, comprehensions and every other node type are
refused. Execution is bounded so a crafted expression cannot spend the turn
computing a gigantic power.
"""

from __future__ import annotations

import ast

from core.tools.tool_registry import ToolContext, ToolSpec, require_string
from schemas.tool_schema import ToolResultSchema

MAX_EXPRESSION_CHARS = 200
MAX_POWER_EXPONENT = 64
MAX_MAGNITUDE = 1e100

_BINARY_OPERATORS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}
_UNARY_OPERATORS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


def _guard(value: float | int) -> float | int:
    if isinstance(value, complex):
        raise ValueError("the result is not a real number")
    if isinstance(value, int):
        if value.bit_length() > 400:
            raise ValueError("the result is too large to compute")
        return value
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("the result is not a finite number")
    if abs(value) > MAX_MAGNITUDE:
        raise ValueError("the result is too large to compute")
    return value


def _evaluate(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numbers are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        operator = _BINARY_OPERATORS.get(type(node.op))
        if operator is None:
            raise ValueError(f"the operator {type(node.op).__name__} is not allowed")
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if not isinstance(right, int) or abs(right) > MAX_POWER_EXPONENT:
                raise ValueError(
                    f"an exponent must be a whole number no larger than {MAX_POWER_EXPONENT}"
                )
        try:
            return _guard(operator(left, right))
        except ZeroDivisionError as error:
            raise ValueError("division by zero") from error
    if isinstance(node, ast.UnaryOp):
        operator = _UNARY_OPERATORS.get(type(node.op))
        if operator is None:
            raise ValueError(f"the operator {type(node.op).__name__} is not allowed")
        return _guard(operator(_evaluate(node.operand)))
    raise ValueError(f"{type(node).__name__} is not allowed in an arithmetic expression")


def evaluate_expression(expression: str) -> float | int:
    """Evaluate a whitelisted arithmetic expression, or raise ValueError."""
    text = expression.strip()
    if not text:
        raise ValueError("the expression is empty")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise ValueError(f"the expression is longer than {MAX_EXPRESSION_CHARS} characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as error:
        raise ValueError("the expression is not valid arithmetic") from error
    return _evaluate(tree.body)


def format_number(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    if value.is_integer() and abs(value) < 1e16:
        return str(int(value))
    return f"{value:.10g}"


async def _calculate(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
    expression = require_string(arguments, "expression")
    try:
        value = evaluate_expression(expression)
    except ValueError as error:
        return ToolResultSchema.failure("calculate", str(error))
    return ToolResultSchema.success("calculate", f"{expression} = {format_number(value)}")


def build_calculator_tool() -> ToolSpec:
    """Arithmetic is local and pure, so it gets no circuit breaker.

    A breaker isolates a remote failure domain. There is no network here and no
    shared dependency that can go down, so wrapping it would only add latency
    and hide bugs. The registry still applies a timeout, which is what bounds a
    pathological expression.
    """
    return ToolSpec(
        name="calculate",
        description=(
            "Evaluate one arithmetic expression and return the number. Supports "
            "numbers, parentheses and the operators + - * / // % ** with unary "
            "plus and minus. It is not a programming language: no variables, no "
            "functions, no units, no dates and no text. Exponents must be whole "
            "numbers no larger than 64, and expressions are capped at "
            f"{MAX_EXPRESSION_CHARS} characters. Use it for exact arithmetic "
            "instead of doing the sum yourself."
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The arithmetic to evaluate, for example (12*7)+3/2.",
                }
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        handler=_calculate,
        timeout_seconds=2.0,
    )
