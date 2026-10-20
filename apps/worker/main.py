import asyncio
import hashlib
import logging
import signal

from sqlalchemy import func, select

from lifeos.core.config import get_settings
from lifeos.core.observability import safe_error, setup
from lifeos.integrations.n8n import N8nClient
from lifeos.persistence.database import create_database
from lifeos.persistence.store import Store
from lifeos.services.router import CommandRouter
from lifeos.services.runtime import postgres_runtime

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, runtime, stop):
        self.store, self.runtime, self.stop = store, runtime, stop
        self.router = CommandRouter(store)
        self.n8n = N8nClient(store.settings)

    async def process(self, job):
        if job.kind == "command":
            try:
                response = await self.router.dispatch(job.payload)
            except (ValueError, PermissionError) as error:
                response = str(error)
            await self.store.enqueue(
                "notify",
                f"command-response:{job.id}",
                {"target": job.payload["target"], "text": response},
            )
        elif job.kind == "n8n":
            await self.n8n.invoke(job.payload["workflow"], job.payload["data"], job.dedupe_key)
        elif job.kind == "run":
            # A transaction advisory lock survives across graph stages, but releases
            # automatically if the worker crashes. Separate session, no table locks.
            async with self.store.sessions.begin() as lock_session:
                key = int.from_bytes(
                    hashlib.sha256(str(job.run_id).encode()).digest()[:8], "big", signed=True
                )
                locked = await lock_session.scalar(select(func.pg_try_advisory_xact_lock(key)))
                if not locked:
                    raise RuntimeError("Run is already executing")
                await self.runtime.execute(job.run_id, job.payload)

    async def loop(self):
        while not self.stop.is_set():
            await self.store.expire_approvals()
            job = await self.store.claim(["run", "command", "n8n"])
            if not job:
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=1)
                except TimeoutError:
                    pass
                continue
            try:
                await self.process(job)
                await self.store.finish_job(job.id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                safe = safe_error(error)
                logger.error("Job %s failed: %s", job.id, safe)
                status = await self.store.finish_job(job.id, safe)
                if job.kind == "run":
                    if status == "queued":
                        await self.store.set_run(job.run_id, "queued", error=safe)
                        continue
                    await self.store.set_run(job.run_id, "failed", error=safe)
                    run = await self.store.get_run(job.run_id)
                    await self.store.notify(
                        run,
                        f"Run `{run.id}` failed: {safe}. Check configuration or revise the request.",
                        f"error:{job.attempts}",
                    )


async def heartbeat(stop):
    from pathlib import Path

    while not stop.is_set():
        await asyncio.to_thread(Path("/tmp/lifeos-worker-heartbeat").touch)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass


async def main():
    settings = get_settings()
    settings.require_runtime()
    setup(settings)
    engine, sessions = create_database(settings)
    store = Store(sessions, settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        loop.add_signal_handler(sig, stop.set)
    try:
        async with postgres_runtime(settings, store) as runtime:
            worker = Worker(store, runtime, stop)
            await asyncio.gather(
                heartbeat(stop), *(worker.loop() for _ in range(settings.worker_concurrency))
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
