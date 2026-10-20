import asyncio
import base64
import io
import os
import re
import wave
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from lifeos.core.observability import operation

MAX_MEDIA_BYTES = 30 * 1024 * 1024


async def atomic_write(destination: Path, data: bytes):
    if len(data) > MAX_MEDIA_BYTES:
        raise ValueError("Media response too large")

    def write():
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, destination)

    await asyncio.to_thread(write)


class MockImages:
    async def generate(self, prompt, destination, key):
        def create():
            image = Image.new("RGB", (1080, 1920), (24, 40, 70))
            ImageDraw.Draw(image).text((50, 100), "MOCK IMAGE - TEST ASSET", fill="white")
            output = io.BytesIO()
            image.save(output, "PNG")
            return output.getvalue()

        await atomic_write(destination, await asyncio.to_thread(create))


class MockTTS:
    async def synthesize(self, text, destination, key, duration):
        # Silence is explicitly mock audio, never presented as generated speech.
        def create():
            output = io.BytesIO()
            with wave.open(output, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\0\0" * int(duration * 16000))
            return output.getvalue()

        await atomic_write(destination, await asyncio.to_thread(create))


class OpenAIImages:
    def __init__(self, settings, client=None):
        self.settings, self.client = settings, client

    async def generate(self, prompt, destination, key):
        if not self.settings.image_api_key.get_secret_value():
            raise ValueError("IMAGE_API_KEY required")
        client = self.client or httpx.AsyncClient(timeout=self.settings.media_timeout_seconds)
        try:
            with operation("media.image"):
                response = await client.post(
                    self.settings.image_base_url + "/images/generations",
                    headers={
                        "Authorization": "Bearer " + self.settings.image_api_key.get_secret_value(),
                        "Idempotency-Key": key,
                    },
                    json={
                        "model": self.settings.image_model,
                        "prompt": prompt,
                        "size": "1024x1536",
                        "n": 1,
                        "response_format": "b64_json",
                    },
                )
                response.raise_for_status()
                if len(response.content) > MAX_MEDIA_BYTES * 2:
                    raise ValueError("Image response too large")
                # URL responses are deliberately unsupported: no model-selected fetch URLs.
                data = base64.b64decode(response.json()["data"][0]["b64_json"], validate=True)

                def verify():
                    with Image.open(io.BytesIO(data)) as image:
                        image.verify()

                await asyncio.to_thread(verify)
                await atomic_write(destination, data)
        finally:
            if self.client is None:
                await client.aclose()


class ElevenLabs:
    def __init__(self, settings, client=None):
        self.settings, self.client = settings, client

    async def synthesize(self, text, destination, key, duration):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.settings.elevenlabs_voice_id):
            raise ValueError("ELEVENLABS_VOICE_ID is required and must be a valid identifier")
        if not self.settings.elevenlabs_api_key.get_secret_value():
            raise ValueError("ELEVENLABS_API_KEY required")
        client = self.client or httpx.AsyncClient(timeout=self.settings.media_timeout_seconds)
        try:
            with operation("media.tts"):
                async with client.stream(
                    "POST",
                    f"https://api.elevenlabs.io/v1/text-to-speech/{self.settings.elevenlabs_voice_id}",
                    headers={
                        "xi-api-key": self.settings.elevenlabs_api_key.get_secret_value(),
                        "Idempotency-Key": key,
                    },
                    json={
                        "text": text,
                        "model_id": self.settings.elevenlabs_model_id,
                        "language_code": "hi",
                    },
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_MEDIA_BYTES:
                            raise ValueError("TTS response too large")
                await atomic_write(destination, bytes(data))
        finally:
            if self.client is None:
                await client.aclose()


def create_providers(settings):
    images = {"none": lambda: None, "mock": MockImages, "openai": lambda: OpenAIImages(settings)}[
        settings.image_provider
    ]()
    tts = {"none": lambda: None, "mock": MockTTS, "elevenlabs": lambda: ElevenLabs(settings)}[
        settings.tts_provider
    ]()
    return images, tts
