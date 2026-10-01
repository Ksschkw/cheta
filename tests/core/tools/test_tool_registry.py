"""The registry is the capability list, and it must only list callable tools."""

from __future__ import annotations

import asyncio

from core.container import build_tool_registry
from core.gateways.web_gateway import WebGateway
from core.resilience import Boundary, ResiliencePolicy
from core.tools.tool_registry import ToolContext, ToolRegistry, ToolSpec
from schemas.tool_schema import ToolResultSchema


def _context() -> ToolContext:
    return ToolContext(user_id="u1", namespace="ns", surface="web")


def _spec(name: str, handler) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} does a thing. It is test only.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
    )


async def _echo(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
    return ToolResultSchema.success("echo", f"echo:{arguments.get('value')}")


async def test_the_registry_lists_exactly_the_registered_tools() -> None:
    registry = ToolRegistry([_spec("echo", _echo), _spec("other", _echo)])

    assert registry.names() == ("echo", "other")
    assert [definition.name for definition in registry.definitions()] == ["echo", "other"]
    # A name that was never registered is not in the capability list.
    assert "not_a_tool" not in registry.names()


async def test_every_listed_tool_is_actually_callable() -> None:
    registry = ToolRegistry([_spec("echo", _echo)])

    result = await registry.execute("echo", {"value": "hi"}, _context())

    assert result.ok is True
    assert result.content == "echo:hi"


async def test_an_unregistered_tool_fails_readably_instead_of_raising() -> None:
    registry = ToolRegistry([_spec("echo", _echo)])

    result = await registry.execute("ghost", {}, _context())

    assert result.ok is False
    assert result.name == "ghost"
    assert "no tool named ghost" in (result.error or "")


async def test_a_tool_that_raises_becomes_a_readable_failure() -> None:
    async def explode(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        raise RuntimeError("boom")

    registry = ToolRegistry([_spec("explode", explode)])

    result = await registry.execute("explode", {}, _context())

    assert result.ok is False
    assert "failed" in (result.error or "")


async def test_the_real_registry_contains_only_its_own_definitions() -> None:
    """Built through the composition root, names and definitions must agree."""
    from core.config import Settings

    settings = Settings(database_path=":memory:")
    from core.database import Database
    from crud.memory_crud import MemoryCrud
    from crud.reminder_crud import ReminderCrud

    database = Database(":memory:")
    database.migrate()
    from core.container import build_memory_gateway

    registry = build_tool_registry(
        memories=MemoryCrud(database),
        memory_gateway=build_memory_gateway(settings),
        reminders=ReminderCrud(database),
        web_gateway=WebGateway(
            boundary=Boundary(ResiliencePolicy(dependency="web", max_attempts=1))
        ),
    )

    definitions = [definition.name for definition in registry.definitions()]
    assert definitions == list(registry.names())
    for name in registry.names():
        assert registry.get(name) is not None
        assert registry.get(name).handler is not None
    # The examples from the brief are present, and nothing phantom is.
    assert "calculate" in definitions
    assert "web_search" in definitions
    assert "crawl" in definitions
    assert "not_a_tool" not in definitions


async def test_a_hanging_tool_is_bounded_by_its_own_timeout() -> None:
    async def hang(arguments: dict[str, object], context: ToolContext) -> ToolResultSchema:
        await asyncio.sleep(5)
        return ToolResultSchema.success("hang", "never")

    registry = ToolRegistry(
        [ToolSpec(
            name="hang",
            description="hangs",
            parameters={"type": "object", "properties": {}},
            handler=hang,
            timeout_seconds=0.05,
        )]
    )

    result = await registry.execute("hang", {}, _context())

    assert result.ok is False
    assert "timed out" in (result.error or "")
