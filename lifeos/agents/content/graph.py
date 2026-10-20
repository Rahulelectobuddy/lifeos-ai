import json
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from lifeos.agents.content import prompts
from lifeos.agents.content.schemas import Idea, ProductionManifest, Scene, Script, revise_scene
from lifeos.agents.content.state import ContentState
from lifeos.core.tool_registry import ToolContext


def build_graph(gateway, registry, approvals, production, store, checkpointer):
    async def idea(state):
        result = await gateway.structured(prompts.IDEA, state["request"], Idea)
        return {"idea": result.model_dump(mode="json")}

    async def research(state):
        result = await registry.execute(
            "content_research",
            {"queries": state["idea"]["research_queries"]},
            ToolContext(UUID(state["run_id"]), state["user_id"], frozenset({"research.read"})),
        )
        return {"research": result}

    async def script(state):
        result = await gateway.structured(
            prompts.SCRIPT,
            json.dumps(
                {"request": state["request"], "idea": state["idea"], "research": state["research"]},
                ensure_ascii=False,
            ),
            Script,
        )
        return {"script": result.model_dump(mode="json")}

    async def storyboard(state):
        result = await gateway.structured(
            prompts.STORYBOARD,
            json.dumps(
                {"script": state["script"], "research": state["research"]}, ensure_ascii=False
            ),
            ProductionManifest,
        )
        payload = result.model_dump(mode="json")
        if not state["research"]["configured"]:
            payload["research_notes"].append(
                "Research provider is not configured; verify factual claims manually."
            )
        return {"manifest": ProductionManifest.model_validate(payload).model_dump(mode="json")}

    async def revision(state):
        data = state["input_data"]
        manifest = ProductionManifest.model_validate(data["base_manifest"])
        number = data["scene_number"]
        result = await gateway.structured(
            prompts.REVISION,
            json.dumps(
                {
                    "scene": manifest.scenes[number - 1].model_dump(),
                    "instruction": state["request"],
                },
                ensure_ascii=False,
            ),
            Scene,
        )
        return {"manifest": revise_scene(manifest, number, result).model_dump(mode="json")}

    async def approval(state):
        payload = {
            "manifest": state["manifest"],
            "image_provider": production.settings.image_provider,
            "tts_provider": production.settings.tts_provider,
            "note": "Mock providers generate placeholder images and silent audio. Paid providers may incur charges.",
        }
        return {"approved": await approvals.wait(state, "storyboard", payload)}

    async def assets(state):
        await store.content_metadata(state)
        return await production.execute(registry, state, "media_assets")

    async def voiceover(state):
        keys = await production.execute(registry, state, "media_voiceovers")
        return {"voiceovers": keys, "artifact_keys": [*state["artifact_keys"], *keys]}

    async def captions(state):
        key = await production.captions(state)
        return {"captions": key, "artifact_keys": [*state["artifact_keys"], key]}

    async def render(state):
        key = await production.execute(registry, state, "media_render")
        if key:
            run = await store.get_run(UUID(state["run_id"]))
            await store.enqueue(
                "upload",
                f"review-upload:{run.id}",
                {"target": run.reply_target, "key": key},
                run.id,
            )
        return {
            "rendered": key or "",
            "artifact_keys": [*state["artifact_keys"], *([key] if key else [])],
        }

    async def review(state):
        return {
            "reviewed": await approvals.wait(
                state,
                "final_review",
                {"review_video": state["rendered"], "manifest": state["manifest"]},
            )
        }

    async def final_export(state):
        result = await production.export(state)
        await store.content_metadata({**state, **result})
        return result

    async def reject(_state):
        return {"status": "rejected"}

    graph = StateGraph(ContentState)
    stages = [
        ("idea", idea),
        ("research", research),
        ("script", script),
        ("storyboard", storyboard),
        ("revision", revision),
        ("approval", approval),
        ("assets", assets),
        ("voiceover", voiceover),
        ("captions", captions),
        ("render", render),
        ("review", review),
        ("final_export", final_export),
        ("reject", reject),
    ]
    for name, node in stages:
        graph.add_node(name, node)
    graph.add_conditional_edges(
        START,
        lambda state: "revision" if state.get("input_data", {}).get("base_manifest") else "idea",
    )
    for source, target in [
        ("idea", "research"),
        ("research", "script"),
        ("script", "storyboard"),
        ("storyboard", "approval"),
        ("revision", "approval"),
        ("assets", "voiceover"),
        ("voiceover", "captions"),
        ("captions", "render"),
    ]:
        graph.add_edge(source, target)
    graph.add_conditional_edges(
        "approval", lambda state: "assets" if state["approved"] else "reject"
    )
    graph.add_conditional_edges(
        "render", lambda state: "review" if state.get("rendered") else "final_export"
    )
    graph.add_conditional_edges(
        "review", lambda state: "final_export" if state["reviewed"] else "reject"
    )
    graph.add_edge("final_export", END)
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=checkpointer)
