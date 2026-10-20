import asyncio
import json
from pathlib import Path

import httpx


class ZulipError(RuntimeError):
    pass


class ZulipClient:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.zulip_url + "/api/v1/",
            auth=(settings.zulip_bot_email, settings.zulip_api_key.get_secret_value()),
            timeout=100,
        )

    async def call(self, method, path, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        data = response.json()
        if data.get("result") != "success":
            raise ZulipError("Zulip request rejected")
        return data

    async def register(self):
        return await self.call(
            "POST",
            "register",
            data={
                "event_types": '["message"]',
                "fetch_event_types": '["message"]',
                "apply_markdown": "false",
                "all_public_streams": "false",
            },
        )

    async def verify_private_streams(self):
        if not self.settings.allowed_stream_ids:
            return
        result = await self.call("GET", "users/me/subscriptions")
        private_ids = {
            item["stream_id"] for item in result["subscriptions"] if item.get("invite_only")
        }
        if not set(self.settings.allowed_stream_ids) <= private_ids:
            raise PermissionError("Allowed streams must be subscribed private channels")

    async def events(self, queue_id, last_event_id):
        return await self.call(
            "GET",
            "events",
            params={"queue_id": queue_id, "last_event_id": last_event_id, "dont_block": "false"},
        )

    async def me(self):
        return (await self.call("GET", "users/me"))["user_id"]

    async def send(self, target, text):
        if not target:
            return
        data = {"type": target["type"], "to": json.dumps(target["to"]), "content": text}
        if target["type"] == "stream":
            data["topic"] = target["topic"]
        # Zulip message size is bounded; keep complete proposals across chunks.
        for offset in range(0, len(text), 9000):
            await self.call(
                "POST", "messages", data={**data, "content": text[offset : offset + 9000]}
            )

    async def upload(self, target, path: Path):
        if (await asyncio.to_thread(path.stat)).st_size > 24 * 1024 * 1024:
            raise ValueError(
                "Artifact exceeds the default upload budget; use authenticated API download"
            )
        # Multipart upload is bounded, and only validated artifact paths reach it.
        data = await asyncio.to_thread(path.read_bytes)
        result = await self.call("POST", "user_uploads", files={"file": (path.name, data)})
        await self.send(target, f"[{path.name}]({result['uri']})")

    async def close(self):
        await self.client.aclose()


def reply_target(message, bot_id):
    if message["type"] == "stream":
        return {"type": "stream", "to": message["stream_id"], "topic": message["subject"]}
    participants = [item["id"] for item in message["display_recipient"] if item["id"] != bot_id]
    if len(participants) != 1 or participants[0] != message["sender_id"]:
        raise PermissionError("Use one-to-one DMs to keep personal data private")
    return {"type": "private", "to": participants}


def parse_command(content):
    text = content.strip()
    if text.startswith("@**"):
        _, separator, text = text[3:].partition("**")
        if not separator:
            raise ValueError("Invalid mention")
        text = text.strip()
    if not text.startswith("/"):
        return None
    command, _, arguments = text.partition(" ")
    known = {
        "/help",
        "/agents",
        "/plan",
        "/tasks",
        "/projects",
        "/short",
        "/short-status",
        "/approve",
        "/reject",
        "/status",
    }
    if command not in known:
        raise ValueError("Unknown command; use /help")
    return command, arguments.strip()
