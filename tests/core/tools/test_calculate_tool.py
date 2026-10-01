"""calculate must do arithmetic and must refuse anything eval would accept."""

from __future__ import annotations

import pytest

from core.tools.calculate_tool import build_calculator_tool, evaluate_expression
from core.tools.tool_registry import ToolContext
from schemas.tool_schema import ToolResultSchema

UNSAFE = [
    "__import__('os').system('echo pwned')",
    "open('/etc/passwd').read()",
    "eval('1+1')",
    "(1).__class__",
    "1 if True else 2",
    "[x for x in range(3)]",
    "a + 1",
    "lambda: 1",
    "'1' + '2'",
    "2 ** 9999",
    "9 ** 9 ** 9",
]


@pytest.mark.parametrize("expression", UNSAFE)
def test_eval_style_and_non_whitelisted_input_is_refused(expression: str) -> None:
    with pytest.raises(ValueError):
        evaluate_expression(expression)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 1", 2),
        ("(12*7)+3/2", 85.5),
        ("7 // 2", 3),
        ("7 % 2", 1),
        ("2 ** 10", 1024),
        ("-3 + +5", 2),
        ("10 / 4", 2.5),
    ],
)
def test_arithmetic_is_correct(expression: str, expected: float) -> None:
    assert evaluate_expression(expression) == expected


def test_division_by_zero_is_a_readable_value_error() -> None:
    with pytest.raises(ValueError, match="zero"):
        evaluate_expression("1 / 0")


def test_an_overlong_expression_is_refused() -> None:
    with pytest.raises(ValueError):
        evaluate_expression("1+" * 200 + "1")


async def test_the_tool_formats_the_answer_and_reports_bad_input() -> None:
    spec = build_calculator_tool()
    context = ToolContext(user_id="u1", namespace="ns", surface="web")

    good = await spec.handler({"expression": "6*7"}, context)
    bad = await spec.handler({"expression": "__import__('os')"}, context)

    assert isinstance(good, ToolResultSchema) and good.ok is True
    assert good.content == "6*7 = 42"
    assert bad.ok is False
    # The description states the limit so the model does not over-trust it.
    assert "no variables" in spec.description
