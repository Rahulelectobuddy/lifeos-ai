import asyncio

import pytest
from sqlalchemy import select

from apps.worker.main import Worker
from lifeos.persistence.models import WorkflowEvent


@pytest.mark.parametrize("attempt", [1, 3])
async def test_run_failure_only_becomes_terminal_after_retries(store, attempt):
    run = await store.submit(123, "planning", "Plan", {}, f"worker-retry:{attempt}")
    async with store.sessions.begin() as session:
        job = await session.scalar(select(WorkflowEvent).where(WorkflowEvent.run_id == run.id))
        job.attempts = attempt - 1
    stop = asyncio.Event()
    worker = Worker(store, None, stop)

    async def fail(job):
        stop.set()
        raise RuntimeError("Temporary model outage")

    worker.process = fail
    await worker.loop()
    result = await store.get_run(run.id)
    assert result.status == ("queued" if attempt < 3 else "failed")
    assert (result.ended_at is not None) == (attempt == 3)
    async with store.sessions() as session:
        notifications = list(
            await session.scalars(select(WorkflowEvent).where(WorkflowEvent.kind == "notify"))
        )
    assert len(notifications) == (0 if attempt < 3 else 1)
