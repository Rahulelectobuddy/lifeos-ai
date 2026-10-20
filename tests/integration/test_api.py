from uuid import uuid4

import httpx

from apps.api.main import create_app
from lifeos.integrations.media.storage import LocalStorage


async def test_auth_ownership_and_health(store, settings, monkeypatch):
    monkeypatch.setattr("lifeos.security.access.get_settings", lambda: settings)
    monkeypatch.setattr("apps.api.main.get_settings", lambda: settings)
    app = create_app(store, LocalStorage(settings.artifact_root))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/health/live")).status_code == 200
            payload = {"agent_type": "planning", "request": "Plan", "idempotency_key": str(uuid4())}
            assert (await client.post("/v1/runs", json=payload)).status_code == 401
            headers = {
                "Authorization": "Bearer " + settings.api_token.get_secret_value(),
                "X-Zulip-User-ID": "123",
            }
            result = await client.post("/v1/runs", json=payload, headers=headers)
            assert result.status_code == 202
            run_id = result.json()["run_id"]
            assert (await client.get(f"/v1/runs/{run_id}", headers=headers)).status_code == 200
            headers["X-Zulip-User-ID"] = "456"
            assert (await client.get(f"/v1/runs/{run_id}", headers=headers)).status_code == 404
