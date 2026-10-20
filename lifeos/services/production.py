import json
from uuid import UUID

from pydantic import BaseModel

from lifeos.agents.content.schemas import ProductionManifest
from lifeos.core.tool_registry import ToolContext, ToolDefinition
from lifeos.integrations.media.captions import SceneSubtitles


class MediaInput(BaseModel):
    manifest: ProductionManifest


class ProductionService:
    def __init__(self, settings, store, storage, images, tts, renderer):
        self.settings, self.store, self.storage = settings, store, storage
        self.images, self.tts, self.renderer = images, tts, renderer

    async def approved(self, run_id, user_id):
        run = await self.store.get_run(run_id, user_id)
        async with self.store.sessions() as session:
            from sqlalchemy import select

            from lifeos.persistence.models import ApprovalRequest

            approval = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == run_id,
                    ApprovalRequest.stage == "storyboard",
                    ApprovalRequest.status == "approved",
                )
            )
            if not approval or approval.user_id != run.user_id:
                raise PermissionError("Storyboard approval required before media generation")

    async def record(self, run_id, filename, category, data=None, metadata=None):
        key = f"{run_id}/{filename}"
        if data is not None:
            await self.storage.put(key, data)
        await self.storage.publish(key)
        await self.store.save_artifact(UUID(str(run_id)), key, category, metadata or {})
        return key

    async def assets(self, args, context):
        await self.approved(context.run_id, context.user_id)
        manifest = args.manifest.model_dump(mode="json")
        keys = [
            await self.record(
                context.run_id,
                "manifest.json",
                "manifest",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
            ),
            await self.record(
                context.run_id,
                "transcript.txt",
                "Transcript",
                manifest["transcript_hindi"].encode(),
            ),
        ]
        image_keys = []
        if self.images:
            for scene in manifest["scenes"]:
                filename = f"scene-{scene['number']:02}.png"
                key = f"{context.run_id}/{filename}"
                path = self.storage.path(key)
                if not path.is_file():
                    await self.images.generate(scene["image_prompt_english"], path, key)
                image_keys.append(
                    await self.record(
                        context.run_id,
                        filename,
                        "Frames",
                        metadata={"mock": self.settings.image_provider == "mock"},
                    )
                )
        return {"artifact_keys": keys + image_keys, "images": image_keys}

    async def voiceovers(self, args, context):
        await self.approved(context.run_id, context.user_id)
        keys = []
        if self.tts:
            for scene in args.manifest.scenes:
                extension = "wav" if self.settings.tts_provider == "mock" else "mp3"
                filename = f"voice-{scene.number:02}.{extension}"
                key = f"{context.run_id}/{filename}"
                path = self.storage.path(key)
                if not path.is_file():
                    await self.tts.synthesize(
                        scene.narration_hindi, path, key, scene.duration_seconds
                    )
                keys.append(
                    await self.record(
                        context.run_id,
                        filename,
                        "voiceover",
                        metadata={"mock": self.settings.tts_provider == "mock"},
                    )
                )
        return keys

    async def render(self, args, context):
        await self.approved(context.run_id, context.user_id)
        if not self.images or not self.tts:
            return None
        manifest = args.manifest.model_dump(mode="json")
        extension = "wav" if self.settings.tts_provider == "mock" else "mp3"
        images = [
            self.storage.path(f"{context.run_id}/scene-{s.number:02}.png")
            for s in args.manifest.scenes
        ]
        voices = [
            self.storage.path(f"{context.run_id}/voice-{s.number:02}.{extension}")
            for s in args.manifest.scenes
        ]
        captions = self.storage.path(f"{context.run_id}/captions.srt")
        destination = self.storage.path(f"{context.run_id}/review.mp4")
        await self.renderer.render(manifest, images, voices, captions, destination)
        return await self.record(
            context.run_id,
            "review.mp4",
            "review_video",
            metadata={
                "mock": self.settings.tts_provider == "mock"
                or self.settings.image_provider == "mock"
            },
        )

    def register(self, registry):
        for name, description, handler in [
            ("media_assets", "Generate approved scene images and manifest", self.assets),
            ("media_voiceovers", "Generate approved Hindi voiceover per scene", self.voiceovers),
            (
                "media_render",
                "Render approved local assets through validated FFmpeg arguments",
                self.render,
            ),
        ]:
            registry.register(
                ToolDefinition(
                    name, description, MediaInput, frozenset({"content.produce"}), "write", handler
                )
            )

    async def execute(self, registry, state, name):
        return await registry.execute(
            name,
            {"manifest": state["manifest"]},
            ToolContext(
                UUID(state["run_id"]),
                state["user_id"],
                frozenset({"content.produce"}),
                approved=True,
            ),
        )

    async def captions(self, state):
        text = SceneSubtitles().generate(state["manifest"])
        return await self.record(
            state["run_id"],
            "captions.srt",
            "captions",
            text.encode(),
            {"timing": "scene-estimated"},
        )

    async def export(self, state):
        if not state.get("rendered"):
            return {"status": "manifest_only", "production_status": "manifest_only"}
        from sqlalchemy import select

        from lifeos.persistence.models import ApprovalRequest

        async with self.store.sessions() as session:
            approval = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == UUID(state["run_id"]),
                    ApprovalRequest.stage == "final_review",
                    ApprovalRequest.status == "approved",
                )
            )
            if not approval or approval.decided_by != state["user_id"]:
                raise PermissionError("Final export requires verified review approval")
        # Final export is a local copy after explicit review approval; no publishing.
        import asyncio
        import shutil

        source = self.storage.path(state["rendered"])
        key = f"{state['run_id']}/final.mp4"
        import os

        def copy():
            destination = self.storage.path(key)
            temporary = destination.with_suffix(".tmp")
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)

        await asyncio.to_thread(copy)
        await self.record(state["run_id"], "final.mp4", "final_video")
        return {
            "status": "completed",
            "production_status": "final",
            "artifact_keys": [*state.get("artifact_keys", []), key],
        }
