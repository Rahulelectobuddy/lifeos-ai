import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lifeos.agents.content.schemas import ProductionManifest
from lifeos.core.config import Settings
from lifeos.persistence.models import Base
from lifeos.persistence.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        allowed_user_ids=[123, 456],
        api_token="x" * 40,
        dgx_api_key="test",
        dgx_model="test-model",
        artifact_root=tmp_path / "artifacts",
    )


@pytest_asyncio.fixture
async def store(settings, tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        execution_options={"schema_translate_map": {"lifeos": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = Store(async_sessionmaker(engine, expire_on_commit=False), settings)
    yield store
    await engine.dispose()


@pytest.fixture
def manifest():
    narration = [
        "एक छोटी आदत आपके जीवन में बड़ा बदलाव ला सकती है।",
        "हर सुबह सिर्फ पाँच मिनट अपने लक्ष्य के लिए निकालिए।",
        "लगातार छोटे कदम आपको धीरे धीरे मंजिल तक पहुँचाते हैं।",
        "कठिन दिन पर भी प्रयास की छोटी शुरुआत जरूर कीजिए।",
        "आज एक काम चुनिए और अपनी नई शुरुआत अभी कीजिए।",
    ]
    return ProductionManifest(
        title="छोटी आदतें",
        transcript_hindi=" ".join(narration),
        duration_seconds=55,
        scenes=[
            {
                "number": i + 1,
                "duration_seconds": 11,
                "narration_hindi": text,
                "image_prompt_english": "Vertical cinematic illustration of daily habits",
                "production_instruction_english": "Slow push in on the subject",
                "transition": "fade",
            }
            for i, text in enumerate(narration)
        ],
    )


class FakeGateway:
    def __init__(self, manifest=None, planning=None):
        self.manifest, self.planning, self.calls = manifest, planning, []

    async def structured(self, system, request, schema):
        import json

        self.calls.append(schema.__name__)
        if schema.__name__ == "PlanProposal":
            if self.planning:
                return schema.model_validate(self.planning)
            context = json.loads(request)["context"]
            return schema(
                week_start=context["today"],
                summary="No existing tasks; provide project details.",
                items=[],
                questions=["Which project should we plan?"],
            )
        if schema.__name__ == "Idea":
            return schema(title="Habits", angle="Small steps", research_queries=["habit formation"])
        if schema.__name__ == "Script":
            return schema(
                title=self.manifest.title,
                transcript_hindi=self.manifest.transcript_hindi,
                duration_seconds=55,
            )
        if schema.__name__ == "ProductionManifest":
            return self.manifest
        if schema.__name__ == "Scene":
            scene = json.loads(request)["scene"]
            scene["image_prompt_english"] = (
                "A revised cinematic vertical scene about a small daily habit"
            )
            return schema.model_validate(scene)
        raise AssertionError(schema)
