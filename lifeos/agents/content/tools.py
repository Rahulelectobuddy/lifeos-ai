from pydantic import BaseModel, Field

from lifeos.core.tool_registry import ToolDefinition


class ResearchInput(BaseModel):
    queries: list[str] = Field(max_length=3)


def register_tools(registry, research):
    async def search(args, _context):
        return await research.search(args.queries)

    registry.register(
        ToolDefinition(
            "content_research",
            "Query the configured fixed research endpoint",
            ResearchInput,
            frozenset({"research.read"}),
            "read",
            search,
        )
    )
