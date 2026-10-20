import asyncio
from collections.abc import AsyncIterator
from typing import Protocol, TypeVar

import httpx
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from lifeos.core.config import Settings
from lifeos.core.observability import costs, operation, tokens

T = TypeVar("T", bound=BaseModel)


class Gateway(Protocol):
    async def structured(self, system: str, request: str, schema: type[T]) -> T: ...
    def stream(self, system: str, request: str) -> AsyncIterator[str]: ...


class ModelUnavailable(RuntimeError):
    pass


class ModelGateway:
    def __init__(self, settings: Settings, model=None):
        self.settings = settings
        self.semaphore = asyncio.Semaphore(settings.model_concurrency)
        self.model = model or ChatOpenAI(
            base_url=settings.dgx_base_url,
            api_key=settings.dgx_api_key.get_secret_value() or "unconfigured",
            model=settings.dgx_model,
            temperature=settings.model_temperature,
            max_tokens=settings.model_max_tokens,
            timeout=settings.model_timeout_seconds,
            max_retries=settings.model_retries,
        )

    async def structured(self, system: str, request: str, schema: type[T]) -> T:
        prompt = ChatPromptTemplate.from_messages([("system", system), ("human", "{request}")])
        chain = prompt | self.model.with_structured_output(
            schema, method=self.settings.structured_output_method, include_raw=True
        )
        async with self.semaphore:
            with operation("model.structured", model=self.settings.dgx_model):
                try:
                    async with asyncio.timeout(
                        self.settings.model_timeout_seconds * (self.settings.model_retries + 1)
                    ):
                        result = await chain.ainvoke({"request": request})
                    if result.get("parsing_error") or result.get("parsed") is None:
                        raise ValueError("Invalid structured model output")
                    usage = getattr(result["raw"], "usage_metadata", None) or {}
                    for key in ["input_tokens", "output_tokens"]:
                        tokens.add(
                            usage.get(key, 0), {"type": key, "model": self.settings.dgx_model}
                        )
                    estimate = (
                        usage.get("input_tokens", 0) * self.settings.model_input_cost_per_million
                        + usage.get("output_tokens", 0)
                        * self.settings.model_output_cost_per_million
                    ) / 1_000_000
                    costs.add(estimate, {"model": self.settings.dgx_model})
                    return schema.model_validate(result["parsed"])
                except Exception as error:
                    raise ModelUnavailable(
                        "DGX call failed or returned invalid structured output"
                    ) from error

    async def stream(self, system: str, request: str) -> AsyncIterator[str]:
        async with self.semaphore:
            try:
                async with asyncio.timeout(
                    self.settings.model_timeout_seconds * (self.settings.model_retries + 1)
                ):
                    async for chunk in self.model.astream([("system", system), ("human", request)]):
                        if isinstance(chunk.content, str):
                            yield chunk.content
            except Exception as error:
                raise ModelUnavailable("DGX streaming request failed") from error

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    self.settings.dgx_base_url + "/models",
                    headers={
                        "Authorization": "Bearer " + self.settings.dgx_api_key.get_secret_value()
                    },
                )
                return response.is_success
        except httpx.HTTPError:
            return False
