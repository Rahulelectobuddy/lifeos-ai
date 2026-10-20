from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from lifeos.core.config import get_settings
from lifeos.core.observability import operation, setup
from lifeos.integrations.media.storage import create_storage
from lifeos.persistence.database import create_database
from lifeos.persistence.store import Store
from lifeos.security.access import RateLimiter, api_identity


class RunRequest(BaseModel):
    agent_type: Literal["planning", "content"]
    request: str = Field(min_length=1, max_length=8000)
    idempotency_key: UUID


class Decision(BaseModel):
    approve: bool


def create_app(store_override=None, storage_override=None):
    settings = get_settings()
    limiter = RateLimiter(settings.rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(app):
        setup(settings)
        if store_override:
            app.state.store, app.state.storage = store_override, storage_override
            yield
            return
        settings.require_runtime()
        engine, sessions = create_database(settings)
        app.state.store, app.state.engine = Store(sessions, settings), engine
        app.state.storage = create_storage(settings)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="LifeOS AI", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def trace_request(request, call_next):
        with operation("api.request", method=request.method):
            return await call_next(request)

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(request: Request):
        try:
            async with request.app.state.store.sessions() as session:
                await session.execute(text("SELECT 1 FROM lifeos.agent_runs LIMIT 1"))
            return {"status": "ready"}
        except Exception:
            raise HTTPException(503, "Database or migrations unavailable") from None

    @app.post("/v1/runs", status_code=202)
    async def submit(body: RunRequest, request: Request, user_id: int = Depends(api_identity)):
        try:
            limiter.check(user_id)
            run = await request.app.state.store.submit(
                user_id,
                body.agent_type,
                body.request,
                {"type": "private", "to": [user_id]},
                f"api:{user_id}:{body.idempotency_key}",
            )
            return {"run_id": str(run.id), "status": run.status}
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.get("/v1/runs/{run_id}")
    async def status(run_id: UUID, request: Request, user_id: int = Depends(api_identity)):
        try:
            run = await request.app.state.store.get_run(run_id, user_id)
            artifacts = await request.app.state.store.artifacts(run_id, user_id)
            return {
                "run_id": str(run.id),
                "status": run.status,
                "output": run.output,
                "error": run.error,
                "artifacts": [
                    {"id": str(item.id), "key": item.key, "category": item.category}
                    for item in artifacts
                ],
            }
        except ValueError:
            raise HTTPException(404, "Run not found") from None

    @app.post("/v1/approvals/{approval_id}")
    async def decide(
        approval_id: UUID, body: Decision, request: Request, user_id: int = Depends(api_identity)
    ):
        try:
            approval = await request.app.state.store.decide(approval_id, user_id, body.approve)
            return {"status": approval.status, "run_id": str(approval.run_id)}
        except PermissionError:
            raise HTTPException(403, "Approval ownership mismatch") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/v1/artifacts/{artifact_id}")
    async def download(artifact_id: UUID, request: Request, user_id: int = Depends(api_identity)):
        from lifeos.persistence.models import ContentArtifact

        async with request.app.state.store.sessions() as session:
            artifact = await session.get(ContentArtifact, artifact_id)
        if not artifact:
            raise HTTPException(404, "Artifact not found")
        try:
            await request.app.state.store.get_run(artifact.run_id, user_id)
            path = request.app.state.storage.path(artifact.key)
            if not path.is_file():
                raise ValueError("Missing artifact")
            return FileResponse(path, filename=path.name)
        except ValueError:
            raise HTTPException(404, "Artifact not found") from None

    return app


app = create_app()
