import asyncio

from sqlalchemy import func, select

from apps.zulip_bot.main import poll
from lifeos.persistence.models import WorkflowEvent
from lifeos.services.router import CommandRouter


async def test_bot_events_are_durable_deduplicated_and_allowlisted(store):
    stop = asyncio.Event()
    message = {
        "id": 99,
        "sender_id": 123,
        "type": "private",
        "content": "/plan Plan next week",
        "display_recipient": [{"id": 123}, {"id": 11}],
    }

    class FakeZulip:
        calls = 0

        async def verify_private_streams(self):
            pass

        async def me(self):
            return 11

        async def register(self):
            return {"queue_id": "queue", "last_event_id": 1}

        async def events(self, queue_id, last_event_id):
            self.calls += 1
            if self.calls == 2:
                stop.set()
            # Replay the same authorized message and include an unauthorized sender.
            return {
                "events": [
                    {"id": 2, "type": "message", "message": message},
                    {
                        "id": 3,
                        "type": "message",
                        "message": {**message, "id": 100, "sender_id": 789},
                    },
                ]
            }

    await poll(FakeZulip(), store, stop)
    async with store.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WorkflowEvent)
                .where(WorkflowEvent.kind == "command")
            )
            == 1
        )
        event = await session.scalar(select(WorkflowEvent).where(WorkflowEvent.kind == "command"))
    reply = await CommandRouter(store).dispatch(event.payload)
    assert "Queued planning run" in reply
