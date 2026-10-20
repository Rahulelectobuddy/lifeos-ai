from pydantic import BaseModel

from lifeos.core.tool_registry import ToolContext, ToolDefinition


class NoInput(BaseModel):
    pass


def register_tools(registry, store):
    async def context(_args, execution):
        return await store.planning_context(execution.user_id)

    registry.register(
        ToolDefinition(
            "planning_context",
            "Read the user's tasks, projects and preferences",
            NoInput,
            frozenset({"planning.read"}),
            "read",
            context,
        )
    )


async def load_context(registry, state):
    from uuid import UUID

    return await registry.execute(
        "planning_context",
        {},
        ToolContext(UUID(state["run_id"]), state["user_id"], frozenset({"planning.read"})),
    )
