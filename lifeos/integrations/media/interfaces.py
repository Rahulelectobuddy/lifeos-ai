from pathlib import Path
from typing import Protocol


class ImageProvider(Protocol):
    async def generate(self, prompt: str, destination: Path, key: str) -> None: ...


class TTSProvider(Protocol):
    async def synthesize(self, text: str, destination: Path, key: str, duration: float) -> None: ...


class SubtitleProvider(Protocol):
    def generate(self, manifest: dict) -> str: ...


class ArtifactStorage(Protocol):
    def path(self, key: str) -> Path: ...
    async def put(self, key: str, data: bytes) -> Path: ...
    async def publish(self, key: str) -> None: ...
