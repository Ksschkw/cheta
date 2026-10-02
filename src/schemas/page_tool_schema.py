"""Transport DTOs for planning one page tool call from plain words.

A page registers its own tools in the browser, so the server never runs them.
What it can do is read a plain request and decide which of the tools the caller
offered would satisfy it, and with what arguments. The caller (the extension)
runs the tool in the page and owns any confirmation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PageToolSchema(BaseModel):
    """One tool a page offered, described the way the page described it."""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=300)
    # The JSON schema the page supplied for this tool's arguments. It is
    # untrusted page data and is only used to describe the shape to the model.
    input_schema: dict | None = None
    # True when running this tool can change state or spend money, as judged by
    # the caller. The server only echoes it back so the caller can confirm.
    state_changing: bool = False


class PagePlanRequestSchema(BaseModel):
    """One plain request, plus the tools the page currently offers."""

    request: str = Field(min_length=1, max_length=2000)
    tools: list[PageToolSchema] = Field(default_factory=list, max_length=25)


class PagePlanStepSchema(BaseModel):
    """One planned step in a single or multi-step page tool sequence."""

    tool: str = Field(min_length=1, max_length=120)
    arguments: dict = Field(default_factory=dict)
    explanation: str = ""


class PagePlanSchema(BaseModel):
    """Either a tool to run, or no tool at all, or a sequence of steps.

    ``tool`` is a name from the offered list or None. ``arguments`` is only
    meaningful when ``tool`` is set, and is always a JSON object. The caller
    treats every field as untrusted and re-validates against the page's schema.
    ``steps`` contains the ordered sequence of tool steps for multi-step tasks.
    """

    tool: str | None = None
    arguments: dict = Field(default_factory=dict)
    explanation: str = ""
    steps: list[PagePlanStepSchema] = Field(default_factory=list)
