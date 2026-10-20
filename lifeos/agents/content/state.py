from typing import Any, TypedDict


class ContentState(TypedDict, total=False):
    run_id: str
    user_id: int
    request: str
    input_data: dict[str, Any]
    idea: dict[str, Any]
    research: dict[str, Any]
    script: dict[str, Any]
    manifest: dict[str, Any]
    approved: bool
    reviewed: bool
    production_status: str
    images: list[str]
    voiceovers: list[str]
    captions: str
    rendered: str
    artifact_keys: list[str]
    status: str
