import shutil
from uuid import uuid4

import pytest

from lifeos.integrations.media.captions import SceneSubtitles
from lifeos.integrations.media.providers import MockImages, MockTTS
from lifeos.integrations.media.render import FFmpegRenderer, RenderError
from lifeos.integrations.media.storage import LocalStorage


async def test_executable_failure_is_handled(settings, tmp_path):
    settings.ffmpeg_path = "/usr/bin/false"
    with pytest.raises(RenderError):
        await FFmpegRenderer(settings).execute([settings.ffmpeg_path], tmp_path)


@pytest.mark.ffmpeg
async def test_real_mock_media_render(settings, manifest):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Install FFmpeg and ffprobe to validate a full 55-second mock render")
    storage = LocalStorage(settings.artifact_root)
    run_id = uuid4()
    images, voices = [], []
    for scene in manifest.scenes:
        image = storage.path(f"{run_id}/scene-{scene.number:02}.png")
        voice = storage.path(f"{run_id}/voice-{scene.number:02}.wav")
        await MockImages().generate("placeholder", image, str(run_id))
        await MockTTS().synthesize(
            scene.narration_hindi, voice, str(run_id), scene.duration_seconds
        )
        images.append(image)
        voices.append(voice)
    captions = await storage.put(
        f"{run_id}/captions.srt", SceneSubtitles().generate(manifest.model_dump()).encode()
    )
    output = storage.path(f"{run_id}/final.mp4")
    renderer = FFmpegRenderer(settings)
    await renderer.render(manifest.model_dump(), images, voices, captions, output)
    assert output.stat().st_size > 0
    assert 50 <= float((await renderer.probe(output))["format"]["duration"]) <= 60
