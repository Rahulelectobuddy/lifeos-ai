import pytest
from pydantic import ValidationError

from lifeos.agents.content.schemas import ProductionManifest, Script, revise_scene
from lifeos.integrations.media.captions import SceneSubtitles


def test_hindi_and_timings(manifest):
    assert manifest.duration_seconds == 55
    assert manifest.width == 1080 and manifest.height == 1920
    with pytest.raises(ValidationError, match="Hindi"):
        Script(
            title="Invalid",
            transcript_hindi="English script with enough characters to exceed the lower limit.",
            duration_seconds=55,
        )
    data = manifest.model_dump()
    data["scenes"][0]["duration_seconds"] = 9
    with pytest.raises(ValidationError, match="timing"):
        ProductionManifest.model_validate(data)


def test_revision_changes_only_one_scene(manifest):
    scene = manifest.scenes[1].model_copy(
        update={"image_prompt_english": "A beautiful revised visual scene in cinematic style"}
    )
    result = revise_scene(manifest, 2, scene)
    assert result.scenes[1] != manifest.scenes[1]
    assert all(result.scenes[i] == manifest.scenes[i] for i in [0, 2, 3, 4])
    with pytest.raises(ValueError, match="duration"):
        revise_scene(manifest, 2, scene.model_copy(update={"duration_seconds": 10}))


def test_caption_timing(manifest):
    result = SceneSubtitles().generate(manifest.model_dump())
    assert "00:00:00,000" in result and "00:00:55,000" in result
    assert "आदत" in result
