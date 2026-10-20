import httpx
from pydantic import BaseModel, Field


class ResearchResult(BaseModel):
    summary: str = Field(max_length=12000)
    sources: list[str] = Field(default_factory=list, max_length=20)


class ResearchClient:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    async def search(self, queries):
        if self.settings.research_provider == "none":
            return {
                "configured": False,
                "summary": "Research unavailable. Facts require manual verification.",
                "sources": [],
            }
        if not self.settings.research_url or not self.settings.research_url.startswith("https://"):
            raise ValueError("Configured research requires a fixed HTTPS endpoint")
        client = self.client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                self.settings.research_url,
                headers={
                    "Authorization": "Bearer " + self.settings.research_token.get_secret_value()
                },
                json={"queries": queries},
            )
            response.raise_for_status()
            return {
                "configured": True,
                **ResearchResult.model_validate(response.json()).model_dump(),
            }
        finally:
            if self.client is None:
                await client.aclose()
