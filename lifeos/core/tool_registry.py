import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from lifeos.core.observability import operation, safe_error


@dataclass(frozen=True)
class ToolContext:
    run_id: UUID
    user_id: int
    permissions: frozenset[str]
    approved: bool = False


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: type[BaseModel]
    permissions: frozenset[str]
    classification: Literal["read", "write"]
    handler: Callable[[BaseModel, ToolContext], Awaitable[Any]]


class ToolRegistry:
    def __init__(self, audit):
        self.tools: dict[str, ToolDefinition] = {}
        self.audit = audit

    def register(self, tool: ToolDefinition):
        if tool.name in self.tools:
            raise ValueError("Duplicate tool name")
        self.tools[tool.name] = tool

    async def execute(self, name: str, args: dict, context: ToolContext):
        tool = self.tools[name]
        if not tool.permissions <= context.permissions:
            raise PermissionError("Tool permission denied")
        if tool.classification == "write" and not context.approved:
            raise PermissionError("Persistent writes require verified approval")
        validated = tool.input_schema.model_validate(args)
        start = time.perf_counter()
        status, error = "success", None
        try:
            with operation("tool.execute", tool=name):
                return await tool.handler(validated, context)
        except Exception as exc:
            status, error = "failed", safe_error(exc)
            raise
        finally:
            await self.audit(
                context.run_id,
                name,
                tool.classification,
                status,
                int((time.perf_counter() - start) * 1000),
                error,
            )

    def langchain_tools(self, context: ToolContext) -> list[StructuredTool]:
        result = []
        for definition in self.tools.values():
            if definition.permissions <= context.permissions and (
                definition.classification == "read" or context.approved
            ):

                def wrapper(name):
                    async def invoke(**kwargs):
                        return await self.execute(name, kwargs, context)

                    return invoke

                result.append(
                    StructuredTool.from_function(
                        name=definition.name,
                        description=definition.description,
                        args_schema=definition.input_schema,
                        coroutine=wrapper(definition.name),
                    )
                )
        return result
