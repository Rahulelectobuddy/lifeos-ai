"""Optional real PostgreSQL test. Requires an isolated database with migrations applied."""

import os
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from lifeos.core.config import Settings
from lifeos.persistence.database import create_database
from lifeos.persistence.store import Store
from lifeos.services.runtime import Runtime
from tests.conftest import FakeGateway


@pytest.mark.postgres
async def test_real_postgres_restart():
    url = os.environ.get("LIFEOS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set LIFEOS_TEST_DATABASE_URL for an isolated migrated PostgreSQL database")
    settings = Settings(
        _env_file=None,
        database_url=url,
        allowed_user_ids=[123],
        dgx_model="test",
        dgx_api_key="test",
    )
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    checkpoint_url = settings.checkpoint_url
    kwargs = {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": "-c search_path=lifeos",
    }
    try:
        run = await store.submit(123, "planning", "Plan", {}, "postgres-test:" + str(uuid4()))
        async with AsyncConnectionPool(checkpoint_url, kwargs=kwargs) as pool:
            await Runtime(settings, store, FakeGateway(), AsyncPostgresSaver(pool)).execute(
                run.id, {"mode": "start"}
            )
        from sqlalchemy import select

        from lifeos.persistence.models import ApprovalRequest

        async with sessions() as session:
            approval = await session.scalar(
                select(ApprovalRequest).where(ApprovalRequest.run_id == run.id)
            )
        await store.decide(approval.id, 123, True)
        async with AsyncConnectionPool(checkpoint_url, kwargs=kwargs) as pool:
            await Runtime(settings, store, FakeGateway(), AsyncPostgresSaver(pool)).execute(
                run.id, {"mode": "resume", "approval_id": str(approval.id)}
            )
        assert (await store.get_run(run.id)).status == "completed"
    finally:
        await engine.dispose()
