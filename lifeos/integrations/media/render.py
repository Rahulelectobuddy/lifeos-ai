import asyncio
import json
import os
import re

from lifeos.core.observability import operation


class RenderError(RuntimeError):
    pass


class FFmpegRenderer:
    def __init__(self, settings):
        self.settings = settings

    async def execute(self, arguments, cwd):
        # Arguments are constructed exclusively by this adapter, never by a model.
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(self.settings.media_timeout_seconds):
                stdout, _stderr = await process.communicate()
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RenderError("FFmpeg/ffprobe failed; inspect assets and installation")
        return stdout

    async def probe(self, path):
        data = await self.execute(
            [
                self.settings.ffprobe_path,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                path.name,
            ],
            path.parent,
        )
        return json.loads(data)

    async def render(self, manifest, images, voices, captions, destination):
        if destination.is_file():
            return
        if len(images) != len(manifest["scenes"]) or len(voices) != len(images):
            raise RenderError("Every scene requires separate image and voiceover assets")
        if not re.fullmatch(r"[A-Za-z0-9 -]{1,80}", self.settings.subtitle_font):
            raise ValueError("Invalid subtitle font")
        cwd = destination.parent
        for path in [*images, *voices, captions, destination]:
            if path.parent != cwd or not re.fullmatch(r"[A-Za-z0-9_.-]+", path.name):
                raise ValueError("Render assets must have validated local names")
        with operation("media.render"):
            clips = []
            for scene, image, voice in zip(manifest["scenes"], images, voices, strict=True):
                duration = scene["duration_seconds"]
                info = await self.probe(voice)
                actual = float(info["format"]["duration"])
                tempo = actual / duration
                if not 0.5 <= tempo <= 2.0:
                    raise RenderError("Voiceover duration requires script or TTS revision")
                clip = f"clip-{scene['number']:02}.mp4"
                fade = (
                    f",fade=t=in:st=0:d=0.2,fade=t=out:st={max(0, duration - 0.2)}:d=0.2"
                    if scene["transition"] == "fade"
                    else ""
                )
                await self.execute(
                    [
                        self.settings.ffmpeg_path,
                        "-nostdin",
                        "-y",
                        "-v",
                        "error",
                        "-loop",
                        "1",
                        "-i",
                        image.name,
                        "-i",
                        voice.name,
                        "-t",
                        str(duration),
                        "-vf",
                        f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1{fade}",
                        "-af",
                        f"atempo={tempo:.6f},apad,atrim=duration={duration}",
                        "-r",
                        "30",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        clip,
                    ],
                    cwd,
                )
                clips.append(clip)
            listing = cwd / "concat.txt"
            listing.write_text("".join(f"file '{clip}'\n" for clip in clips))
            temporary = cwd / "rendering.mp4"
            style = f"FontName={self.settings.subtitle_font},FontSize=18,MarginV=90,Outline=2"
            await self.execute(
                [
                    self.settings.ffmpeg_path,
                    "-nostdin",
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "1",
                    "-i",
                    listing.name,
                    "-vf",
                    f"subtitles={captions.name}:force_style='{style}'",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-t",
                    str(manifest["duration_seconds"]),
                    "-movflags",
                    "+faststart",
                    temporary.name,
                ],
                cwd,
            )
            info = await self.probe(temporary)
            videos = [stream for stream in info["streams"] if stream["codec_type"] == "video"]
            if (
                not videos
                or videos[0]["width"] != 1080
                or videos[0]["height"] != 1920
                or not 49.9 <= float(info["format"]["duration"]) <= 60.2
            ):
                raise RenderError("Rendered output does not match the Shorts specification")
            os.replace(temporary, destination)
