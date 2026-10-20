import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from lifeos.agents.content.graph import build_graph as content_graph
from lifeos.agents.content.tools import register_tools as content_tools
from lifeos.agents.planning.graph import build_graph as planning_graph
from lifeos.agents.planning.tools import register_tools as planning_tools
from lifeos.core.approvals import ApprovalEngine
from lifeos.core.model_gateway import ModelGateway
from lifeos.core.observability import operation
from lifeos.core.tool_registry import ToolRegistry
from lifeos.integrations.media.providers import create_providers
from lifeos.integrations.media.render import FFmpegRenderer
from lifeos.integrations.media.storage import create_storage
from lifeos.integrations.research import ResearchClient
from lifeos.services.production import ProductionService


class Runtime:
    def __init__(
        self, settings, store, gateway, checkpointer, storage=None, providers=None, renderer=None
    ):
        self.settings, self.store = settings, store
        self.registry = ToolRegistry(store.audit_tool)
        planning_tools(self.registry, store)
        content_tools(self.registry, ResearchClient(settings))
        self.storage = storage or create_storage(settings)
        images, tts = providers or create_providers(settings)
        self.production = ProductionService(
            settings, store, self.storage, images, tts, renderer or FFmpegRenderer(settings)
        )
        self.production.register(self.registry)
        approvals = ApprovalEngine(store)
        self.graphs = {
            "planning": planning_graph(gateway, self.registry, approvals, store, checkpointer),
            "content": content_graph(
                gateway, self.registry, approvals, self.production, store, checkpointer
            ),
        }

    async def execute(self, run_id: UUID, payload: dict):
        run = await self.store.get_run(run_id)
        graph = self.graphs[run.agent_type]
        config = {"configurable": {"thread_id": str(run.id)}, "recursion_limit": 40}
        snapshot = await graph.aget_state(config)
        if snapshot.values and not snapshot.next:
            result = dict(snapshot.values)
        else:
            if payload.get("mode") == "resume":
                pending = [
                    i.value.get("approval_id") for task in snapshot.tasks for i in task.interrupts
                ]
                if pending:
                    if payload.get("approval_id") not in pending:
                        # An old delivered resume must never decide a later approval.
                        return
                    argument = Command(resume={"approval_id": payload["approval_id"]})
                else:
                    # A prior resume may have passed approval and failed downstream.
                    # Continue its durable unfinished tasks without replaying a decision.
                    argument = None
            elif any(task.interrupts for task in snapshot.tasks):
                await self.store.set_run(run.id, "waiting_approval")
                return
            elif snapshot.values:
                argument = None  # Recover the unfinished checkpoint, not the original input.
            else:
                argument = {
                    "run_id": str(run.id),
                    "user_id": run.user_id,
                    "request": run.request,
                    "input_data": run.input_data,
                }
            await self.store.set_run(run.id, "running")
            with operation("agent.run", agent=run.agent_type):
                async with asyncio.timeout(self.settings.run_timeout_seconds):
                    async for update in graph.astream(
                        argument, config, stream_mode="updates", durability="sync"
                    ):
                        for stage in update:
                            if stage != "__interrupt__":
                                await self.store.notify(
                                    run, f"Run `{run.id}`: {stage} complete.", f"stage:{stage}"
                                )
            snapshot = await graph.aget_state(config)
            result = dict(snapshot.values)
        if any(task.interrupts for task in snapshot.tasks):
            await self.store.set_run(run.id, "waiting_approval")
            return
        status = result.get("status", "completed")
        output = {
            key: result[key]
            for key in [
                "proposal",
                "plan_id",
                "manifest",
                "rendered",
                "artifact_keys",
                "production_status",
            ]
            if key in result
        }
        await self.store.set_run(run.id, status, output)
        await self.store.notify(
            run,
            f"Run `{run.id}`: {status}.\n"
            + (
                "Media providers are missing; scripts/storyboard/manifest are ready, but no final video was generated."
                if status == "manifest_only"
                else "Use /status with this run ID to inspect the result."
            ),
            "finished",
        )
        if run.agent_type == "content":
            for key in output.get("artifact_keys", []):
                if key.endswith(("manifest.json", "transcript.txt", "captions.srt", "final.mp4")):
                    await self.store.enqueue(
                        "upload",
                        f"artifact-upload:{key}",
                        {"target": run.reply_target, "key": key},
                        run.id,
                    )
            if status == "completed" and "content_completed" in self.settings.n8n_workflows:
                await self.store.enqueue(
                    "n8n",
                    f"content-complete:{run.id}",
                    {
                        "workflow": "content_completed",
                        "data": {
                            "run_id": str(run.id),
                            "artifacts": output.get("artifact_keys", []),
                        },
                    },
                    run.id,
                )


@asynccontextmanager
async def postgres_runtime(settings, store):
    # Runtime never calls saver.setup(): only migration credentials may create tables.
    async with AsyncConnectionPool(
        settings.checkpoint_url,
        min_size=1,
        max_size=8,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=lifeos",
        },
    ) as pool:
        yield Runtime(settings, store, ModelGateway(settings), AsyncPostgresSaver(pool))
