import asyncio
import logging
import signal

from lifeos.core.config import get_settings
from lifeos.core.observability import safe_error, setup
from lifeos.integrations.media.storage import create_storage
from lifeos.integrations.zulip import ZulipClient, parse_command, reply_target
from lifeos.persistence.database import create_database
from lifeos.persistence.store import Store

logger = logging.getLogger(__name__)


async def poll(client, store, stop):
    await client.verify_private_streams()
    bot_id = await client.me()
    queue_id, last_event_id, delay = None, -1, 1
    while not stop.is_set():
        try:
            if queue_id is None:
                registration = await client.register()
                queue_id, last_event_id = registration["queue_id"], registration["last_event_id"]
            response = await client.events(queue_id, last_event_id)
            for event in response.get("events", []):
                if event.get("type") == "message":
                    message = event["message"]
                    user_id = message["sender_id"]
                    if user_id in store.settings.allowed_user_ids and user_id != bot_id:
                        # Streams must be explicitly allowlisted private channels.
                        if (
                            message["type"] != "stream"
                            or message["stream_id"] in store.settings.allowed_stream_ids
                        ):
                            try:
                                target = reply_target(message, bot_id)
                                try:
                                    command = parse_command(message["content"])
                                except ValueError:
                                    command = (
                                        True  # Route invalid commands for a helpful error reply.
                                    )
                                if command:
                                    await store.enqueue(
                                        "command",
                                        f"zulip-message:{message['id']}",
                                        {
                                            "user_id": user_id,
                                            "target": target,
                                            "content": message["content"],
                                            "source_id": str(message["id"]),
                                        },
                                    )
                            except (PermissionError, ValueError):
                                logger.info(
                                    "Ignored invalid/private-scope message %s", message["id"]
                                )
                # Advance only after durable enqueue. Replay is deduped by message ID.
                last_event_id = max(last_event_id, event["id"])
            delay = 1
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Zulip queue reconnect: %s", safe_error(error))
            # Network failures preserve cursor; expired queues register anew.
            import httpx

            from lifeos.integrations.zulip import ZulipError

            if isinstance(error, ZulipError) or (
                isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 400
            ):
                queue_id = None
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(30, delay * 2)


async def deliver(client, store, storage, stop):
    while not stop.is_set():
        job = await store.claim(["notify", "upload"])
        if not job:
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
            continue
        try:
            if job.kind == "notify":
                await client.send(job.payload["target"], job.payload["text"])
            else:
                try:
                    await client.upload(job.payload["target"], storage.path(job.payload["key"]))
                except ValueError:
                    await client.send(
                        job.payload["target"],
                        f"Artifact `{job.payload['key']}` exceeds the upload budget; retrieve it through the authenticated LifeOS API.",
                    )
            await store.finish_job(job.id)
        except Exception as error:
            await store.finish_job(job.id, safe_error(error))


async def heartbeat(stop):
    from pathlib import Path

    while not stop.is_set():
        await asyncio.to_thread(Path("/tmp/lifeos-bot-heartbeat").touch)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass


async def main():
    settings = get_settings()
    settings.require_runtime()
    if not settings.zulip_bot_email or not settings.zulip_api_key.get_secret_value():
        raise ValueError("Configure ZULIP_BOT_EMAIL and ZULIP_API_KEY")
    setup(settings)
    engine, sessions = create_database(settings)
    store, client, storage = (
        Store(sessions, settings),
        ZulipClient(settings),
        create_storage(settings),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        loop.add_signal_handler(sig, stop.set)
    polling = asyncio.create_task(poll(client, store, stop))
    delivery = asyncio.create_task(deliver(client, store, storage, stop))
    pulse = asyncio.create_task(heartbeat(stop))
    try:
        stopped = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            [polling, delivery, stopped], return_when=asyncio.FIRST_COMPLETED
        )
        if stopped not in done:
            for task in done:
                task.result()  # Fail the process if a required loop stops.
        stopped.cancel()
    finally:
        for task in [polling, delivery, pulse]:
            task.cancel()
        await asyncio.gather(polling, delivery, return_exceptions=True)
        await client.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
