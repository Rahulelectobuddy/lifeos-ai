import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Idea(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    angle: str = Field(max_length=1000)
    research_queries: list[str] = Field(default_factory=list, max_length=3)


class Script(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    transcript_hindi: str = Field(min_length=50, max_length=5000)
    duration_seconds: float = Field(ge=50, le=60)

    @model_validator(mode="after")
    def hindi(self):
        if len(re.findall(r"[\u0900-\u097f]", self.transcript_hindi)) < 20:
            raise ValueError("Transcript must contain Hindi Devanagari text")
        return self


class Scene(StrictModel):
    number: int = Field(ge=1, le=12)
    duration_seconds: float = Field(ge=2, le=15)
    narration_hindi: str = Field(min_length=1, max_length=1000)
    image_prompt_english: str = Field(min_length=10, max_length=2000)
    production_instruction_english: str = Field(max_length=1000)
    transition: Literal["cut", "fade"] = "cut"

    @model_validator(mode="after")
    def languages(self):
        if not re.search(r"[\u0900-\u097f]", self.narration_hindi):
            raise ValueError("Scene narration must be Hindi")
        if re.search(
            r"[\u0900-\u097f]", self.image_prompt_english + self.production_instruction_english
        ):
            raise ValueError("Production instructions must be English")
        return self


class ProductionManifest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    transcript_hindi: str = Field(min_length=50, max_length=5000)
    duration_seconds: float = Field(default=55, ge=50, le=60)
    aspect_ratio: Literal["9:16"] = "9:16"
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920
    voiceover_language: Literal["hi"] = "hi"
    categories: list[str] = Field(default_factory=lambda: ["Transcript", "Frames", "Transitions"])
    scenes: list[Scene] = Field(min_length=4, max_length=12)
    research_notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def timings(self):
        if [scene.number for scene in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("Scenes must have consecutive numbers")
        if abs(sum(scene.duration_seconds for scene in self.scenes) - self.duration_seconds) > 0.05:
            raise ValueError("Scene timing does not match total duration")
        transcript = " ".join(scene.narration_hindi.strip() for scene in self.scenes)

        def normalize(text):
            return " ".join(text.split())

        if normalize(transcript) != normalize(self.transcript_hindi):
            raise ValueError("Scene narration must exactly match the complete Hindi transcript")
        if set(self.categories) != {"Transcript", "Frames", "Transitions"}:
            raise ValueError("Required production categories missing")
        Script(
            title=self.title,
            transcript_hindi=self.transcript_hindi,
            duration_seconds=self.duration_seconds,
        )
        return self


def revise_scene(manifest: ProductionManifest, number: int, revision: Scene) -> ProductionManifest:
    if not 1 <= number <= len(manifest.scenes) or revision.number != number:
        raise ValueError("Invalid revision scene")
    if abs(revision.duration_seconds - manifest.scenes[number - 1].duration_seconds) > 0.01:
        raise ValueError("Revision must preserve scene duration")
    scenes = list(manifest.scenes)
    scenes[number - 1] = revision
    payload = manifest.model_dump()
    payload["scenes"] = [scene.model_dump() for scene in scenes]
    payload["transcript_hindi"] = " ".join(scene.narration_hindi.strip() for scene in scenes)
    return ProductionManifest.model_validate(payload)
