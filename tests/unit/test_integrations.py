import httpx
import pytest

from lifeos.agents.content.schemas import Script
from lifeos.core.model_gateway import ModelGateway, ModelUnavailable
from lifeos.integrations.airflow import AirflowClient
from lifeos.integrations.media.providers import ElevenLabs, OpenAIImages
from lifeos.integrations.n8n import N8nClient
from lifeos.integrations.zulip import ZulipClient


async def test_n8n_allowlist_and_idempotency(settings):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Idempotency-Key"] == "stable"
        return httpx.Response(200, json={"ok": True})

    settings.n8n_token = "test-token"
    # Assignment accepts raw values; reconstruct to apply SecretStr validation.
    settings = type(settings).model_validate(settings.model_dump())
    settings.n8n_workflows = {"planning_reminder": "lifeos/planning"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = N8nClient(settings, client)
        with pytest.raises(PermissionError):
            await adapter.invoke("arbitrary", {}, "stable")
        await adapter.invoke("planning_reminder", {}, "stable")
    assert len(calls) == 1


async def test_airflow_requires_approval(settings):
    with pytest.raises(PermissionError):
        await AirflowClient(settings).trigger("unlisted", {}, "run", False)


async def test_zulip_register_events_and_reply(settings):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("register"):
            return httpx.Response(
                200, json={"result": "success", "queue_id": "q", "last_event_id": 1}
            )
        if request.url.path.endswith("events"):
            return httpx.Response(200, json={"result": "success", "events": []})
        return httpx.Response(200, json={"result": "success", "id": 22})

    async with httpx.AsyncClient(
        base_url="https://zulip.invalid/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        adapter = ZulipClient(settings, client)
        await adapter.register()
        await adapter.events("q", 1)
        await adapter.send({"type": "stream", "to": 12, "topic": "Planning"}, "Hello")
    assert paths == ["/api/v1/register", "/api/v1/events", "/api/v1/messages"]


async def test_provider_failures_are_real(settings, tmp_path):
    settings = type(settings).model_validate(
        {
            **settings.model_dump(),
            "image_api_key": "test",
            "elevenlabs_api_key": "test",
            "elevenlabs_voice_id": "voice",
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await OpenAIImages(settings, client).generate("image", tmp_path / "image.png", "key")
        with pytest.raises(httpx.HTTPStatusError):
            await ElevenLabs(settings, client).synthesize("नमस्ते", tmp_path / "voice.mp3", "key", 10)
    assert not (tmp_path / "image.png").exists() and not (tmp_path / "voice.mp3").exists()


async def test_gateway_unavailable_and_streaming(settings):
    class Broken:
        def with_structured_output(self, schema, **kwargs):
            from langchain_core.runnables import RunnableLambda

            async def fail(value):
                raise TimeoutError()

            return RunnableLambda(fail)

        async def astream(self, messages):
            from langchain_core.messages import AIMessageChunk

            yield AIMessageChunk(content="Hello")
            yield AIMessageChunk(content=" world")

    gateway = ModelGateway(settings, Broken())
    with pytest.raises(ModelUnavailable):
        await gateway.structured("Return JSON", "test", Script)
    assert "".join([text async for text in gateway.stream("system", "test")]) == "Hello world"


async def test_private_stream_validation(settings):
    settings.allowed_stream_ids = [8]

    def handler(request):
        return httpx.Response(
            200,
            json={"result": "success", "subscriptions": [{"stream_id": 8, "invite_only": False}]},
        )

    async with httpx.AsyncClient(
        base_url="https://zulip.invalid/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(PermissionError, match="private"):
            await ZulipClient(settings, client).verify_private_streams()
