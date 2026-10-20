"""Local test double for Zulip and DGX. Never use as a production service."""

import asyncio
import json
import re
import uuid
from urllib.parse import parse_qs

from fastapi import FastAPI, Request

app = FastAPI()
events = []
messages = []
uploads = []
auto_decisions = set()


def event(content):
    event_id = len(events) + 1
    events.append(
        {
            "id": event_id,
            "type": "message",
            "message": {
                "id": event_id,
                "sender_id": 123,
                "type": "private",
                "content": content,
                "display_recipient": [{"id": 123}, {"id": 11}],
            },
        }
    )


def manifest():
    narration = [
        "एक छोटी आदत आपके जीवन में बड़ा बदलाव ला सकती है।",
        "हर सुबह सिर्फ पाँच मिनट अपने लक्ष्य के लिए निकालिए।",
        "लगातार छोटे कदम आपको धीरे धीरे मंजिल तक पहुँचाते हैं।",
        "कठिन दिन पर भी प्रयास की छोटी शुरुआत जरूर कीजिए।",
        "आज एक काम चुनिए और अपनी नई शुरुआत अभी कीजिए।",
    ]
    return {
        "title": "छोटी आदतें",
        "transcript_hindi": " ".join(narration),
        "duration_seconds": 55,
        "aspect_ratio": "9:16",
        "width": 1080,
        "height": 1920,
        "voiceover_language": "hi",
        "categories": ["Transcript", "Frames", "Transitions"],
        "research_notes": [],
        "scenes": [
            {
                "number": i + 1,
                "duration_seconds": 11,
                "narration_hindi": text,
                "image_prompt_english": "Vertical cinematic illustration of a small daily habit",
                "production_instruction_english": "Slow camera push in",
                "transition": "fade",
            }
            for i, text in enumerate(narration)
        ],
    }


event("/plan Plan my next week. I have 90 minutes every weekday for personal projects.")
event("/short A Hindi story about daily habits.")


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "test-model", "object": "model"}]}


@app.post("/v1/chat/completions")
async def completion(request: Request):
    data = await request.json()
    schema = data["response_format"]["json_schema"]["name"]
    production = manifest()
    if schema == "PlanProposal":
        context = json.loads(data["messages"][-1]["content"])["context"]
        value = {
            "week_start": context["today"],
            "summary": "Create a reviewed learning task",
            "items": [],
            "new_tasks": [{"title": "Read a Python chapter", "effort_minutes": 30, "priority": 2}],
            "questions": ["Which project should own this task?"],
        }
    elif schema == "Idea":
        value = {"title": "Habits", "angle": "Small steps", "research_queries": []}
    elif schema == "Script":
        value = {key: production[key] for key in ["title", "transcript_hindi", "duration_seconds"]}
    elif schema == "ProductionManifest":
        value = production
    else:
        raise ValueError("Unexpected fixture schema")
    return {
        "id": "chatcmpl-" + str(uuid.uuid4()),
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
    }


@app.get("/api/v1/users/me")
async def me():
    return {"result": "success", "user_id": 11}


@app.post("/api/v1/register")
async def register():
    return {"result": "success", "queue_id": "fixture-queue", "last_event_id": 0}


@app.get("/api/v1/events")
async def get_events(last_event_id: int = -1):
    result = [item for item in events if item["id"] > last_event_id]
    if not result:
        await asyncio.sleep(0.5)
    return {"result": "success", "events": result}


@app.post("/api/v1/messages")
async def send(request: Request):
    fields = parse_qs((await request.body()).decode())
    text = fields["content"][0]
    messages.append(text)
    # Explicitly test-only approval automation for the disposable account.
    for approval in re.findall(r"Approve: `/approve ([0-9a-f-]{36})`", text):
        if approval not in auto_decisions:
            auto_decisions.add(approval)
            event("/approve " + approval)
    return {"result": "success", "id": len(messages)}


@app.post("/api/v1/user_uploads")
async def upload(request: Request):
    uploads.append(len(await request.body()))
    return {"result": "success", "uri": "/user_uploads/fixture/artifact"}


@app.get("/test/status")
async def test_status():
    return {"messages": messages, "uploads": uploads, "event_count": len(events)}
