from urllib.parse import quote

import httpx


class N8nClient:
    def __init__(self, settings, client=None):
        self.settings, self.client = settings, client

    async def invoke(self, workflow: str, data: dict, idempotency_key: str):
        path = self.settings.n8n_workflows.get(workflow)
        if path is None:
            raise PermissionError("Workflow not allowlisted")
        if not self.settings.n8n_token.get_secret_value():
            raise ValueError("N8N_TOKEN is required")
        if not path or path.startswith("/") or ".." in path or ":" in path or "?" in path:
            raise ValueError("Use a relative webhook identifier")
        client = self.client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                self.settings.n8n_base_url + "/webhook/" + quote(path, safe="/"),
                headers={
                    "Authorization": "Bearer " + self.settings.n8n_token.get_secret_value(),
                    "Idempotency-Key": idempotency_key,
                },
                json=data,
            )
            response.raise_for_status()
            return {"status_code": response.status_code}
        finally:
            if self.client is None:
                await client.aclose()
