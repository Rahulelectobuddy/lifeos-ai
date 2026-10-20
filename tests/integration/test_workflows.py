import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import func, select

from lifeos.core.approvals import ApprovalError
from lifeos.integrations.media.render import RenderError
from lifeos.persistence.models import ApprovalRequest, Task, WeeklyPlan, WorkflowEvent
from lifeos.persistence.store import now
from lifeos.services.router import CommandRouter
from lifeos.services.runtime import Runtime
from tests.conftest import FakeGateway


async def pending(store, run_id, stage=None):
    async with store.sessions() as session:
        query = select(ApprovalRequest).where(
            ApprovalRequest.run_id == run_id, ApprovalRequest.status == "pending"
        )
        if stage:
            query = query.where(ApprovalRequest.stage == stage)
        return await session.scalar(query)


async def test_planning_restart_authorization_and_duplicate(store, settings):
    saver = InMemorySaver()
    run = await store.submit(123, "planning", "Plan next week", {}, "source:1")
    gateway = FakeGateway()
    runtime = Runtime(settings, store, gateway, saver)
    await runtime.execute(run.id, {"mode": "start"})
    approval = await pending(store, run.id)
    assert (await store.get_run(run.id)).status == "waiting_approval"
    assert approval
    with pytest.raises(PermissionError):
        await store.decide(approval.id, 456, True)
    await store.decide(approval.id, 123, True)
    with pytest.raises(ApprovalError, match="already decided"):
        await store.decide(approval.id, 123, True)
    # Reconstruct runtime/graphs around retained checkpoints to simulate process restart.
    restored = Runtime(settings, store, FakeGateway(), saver)
    await restored.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})
    await restored.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})
    assert (await store.get_run(run.id)).status == "completed"
    async with store.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WeeklyPlan)) == 1


async def test_rejected_plan_has_no_business_writes(store, settings):
    run = await store.submit(123, "planning", "Plan", {}, "source:reject")
    runtime = Runtime(settings, store, FakeGateway(), InMemorySaver())
    await runtime.execute(run.id, {"mode": "start"})
    approval = await pending(store, run.id)
    await store.decide(approval.id, 123, False)
    await runtime.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})
    async with store.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WeeklyPlan)) == 0
    assert (await store.get_run(run.id)).status == "rejected"


async def test_content_without_providers_is_manifest_only(store, settings, manifest):
    run = await store.submit(123, "content", "Short about habits", {}, "source:short")
    runtime = Runtime(settings, store, FakeGateway(manifest), InMemorySaver())
    await runtime.execute(run.id, {"mode": "start"})
    approval = await pending(store, run.id, "storyboard")
    assert approval.payload["image_provider"] == "none"
    await store.decide(approval.id, 123, True)
    await runtime.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})
    result = await store.get_run(run.id)
    assert result.status == "manifest_only"
    assert result.output["manifest"]["transcript_hindi"] == manifest.transcript_hindi
    assert result.output["rendered"] == ""
    assert not list(settings.artifact_root.rglob("*.mp4"))
    assert len(await store.artifacts(run.id, 123)) == 3


async def test_parallel_agents_and_message_replay(store, settings, manifest):
    run = await store.submit(123, "planning", "Plan", {}, "source:same")
    duplicate = await store.submit(123, "planning", "Plan", {}, "source:same")
    assert run.id == duplicate.id
    short = await store.submit(123, "content", "Short", {}, "source:other")
    runtime = Runtime(settings, store, FakeGateway(manifest), InMemorySaver())
    await asyncio.gather(
        runtime.execute(run.id, {"mode": "start"}), runtime.execute(short.id, {"mode": "start"})
    )
    assert await pending(store, run.id) and await pending(store, short.id)
    first = await store.enqueue("command", "zulip-message:99", {"user_id": 123})
    assert first
    assert await store.enqueue("command", "zulip-message:99", {"user_id": 123}) is None


async def test_expired_approval_resumes_as_rejection(store, settings):
    run = await store.submit(123, "planning", "Plan", {}, "source:expire")
    runtime = Runtime(settings, store, FakeGateway(), InMemorySaver())
    await runtime.execute(run.id, {"mode": "start"})
    approval = await pending(store, run.id)
    async with store.sessions.begin() as session:
        row = await session.get(ApprovalRequest, approval.id)
        row.expires_at = now() - timedelta(seconds=1)
    with pytest.raises(ApprovalError, match="expired"):
        await store.decide(approval.id, 123, True)
    await store.expire_approvals()
    await runtime.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})
    assert (await store.get_run(run.id)).status == "rejected"


async def test_task_creation_only_after_approval(store, settings):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    proposal = {
        "week_start": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        "summary": "Create a task",
        "items": [],
        "new_tasks": [{"title": "Read a book", "effort_minutes": 30}],
    }
    run = await store.submit(123, "planning", "Create task", {}, "source:create")
    runtime = Runtime(settings, store, FakeGateway(planning=proposal), InMemorySaver())
    await runtime.execute(run.id, {"mode": "start"})
    assert await store.list_records("tasks", 123) == []
    approval = await pending(store, run.id)
    await store.decide(approval.id, 123, True)
    await runtime.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})
    tasks = await store.list_records("tasks", 123)
    assert len(tasks) == 1 and tasks[0]["title"] == "Read a book"


async def test_one_scene_revision_is_new_reviewed_run(store, settings, manifest):
    original = await store.submit(123, "content", "Short", {}, "source:base")
    runtime = Runtime(settings, store, FakeGateway(manifest), InMemorySaver())
    await runtime.execute(original.id, {"mode": "start"})
    router = CommandRouter(store)
    reply = await router.dispatch(
        {
            "user_id": 123,
            "target": {},
            "source_id": "revise",
            "content": f"/short revise {original.id} 2 improve visual",
        }
    )
    async with store.sessions() as session:
        event = await session.scalar(
            select(WorkflowEvent).where(WorkflowEvent.dedupe_key == "request:revise")
        )
    revised = await store.get_run(event.run_id)
    assert revised.id != original.id and str(revised.id) in reply
    await runtime.execute(revised.id, {"mode": "start"})
    approval = await pending(store, revised.id)
    revised_manifest = approval.payload["manifest"]
    assert revised_manifest["scenes"][0] == manifest.model_dump()["scenes"][0]
    assert (
        revised_manifest["scenes"][1]["image_prompt_english"]
        != manifest.scenes[1].image_prompt_english
    )


async def test_media_failure_is_not_success(store, settings, manifest):
    class FailingRenderer:
        async def render(self, *args):
            raise RenderError("Test rendering failure")

    settings.image_provider = "mock"
    settings.tts_provider = "mock"
    run = await store.submit(123, "content", "Short", {}, "source:render-failure")
    runtime = Runtime(
        settings, store, FakeGateway(manifest), InMemorySaver(), renderer=FailingRenderer()
    )
    await runtime.execute(run.id, {"mode": "start"})
    approval = await pending(store, run.id)
    await store.decide(approval.id, 123, True)
    with pytest.raises(RenderError):
        await runtime.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})
    assert not list(settings.artifact_root.rglob("final.mp4"))


async def test_forged_graph_resume_does_not_write(store, settings):
    run = await store.submit(123, "planning", "Plan", {}, "source:forge")
    runtime = Runtime(settings, store, FakeGateway(), InMemorySaver())
    await runtime.execute(run.id, {"mode": "start"})
    approval = await pending(store, run.id)
    with pytest.raises(ApprovalError):
        await runtime.graphs["planning"].ainvoke(
            Command(resume={"approval_id": str(approval.id)}),
            {"configurable": {"thread_id": str(run.id)}},
        )
    assert await pending(store, run.id)
    async with store.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WeeklyPlan)) == 0


@pytest.mark.parametrize("approve_final", [True, False])
async def test_final_review_controls_export(store, settings, manifest, approve_final):
    class TestRenderer:
        async def render(self, manifest, images, voices, captions, destination):
            # Adapter contract fixture, not a real video. Real media is separately marked.
            destination.write_bytes(b"fixture-review-artifact")

    settings.image_provider, settings.tts_provider = "mock", "mock"
    run = await store.submit(123, "content", "Short", {}, "source:final")
    runtime = Runtime(
        settings, store, FakeGateway(manifest), InMemorySaver(), renderer=TestRenderer()
    )
    await runtime.execute(run.id, {"mode": "start"})
    storyboard = await pending(store, run.id, "storyboard")
    await store.decide(storyboard.id, 123, True)
    await runtime.execute(run.id, {"mode": "resume", "approval_id": str(storyboard.id)})
    review = await pending(store, run.id, "final_review")
    assert review and not list(settings.artifact_root.rglob("final.mp4"))
    # A replay of the storyboard resume must not decide the final review.
    await runtime.execute(run.id, {"mode": "resume", "approval_id": str(storyboard.id)})
    assert (await store.get_approval(review.id)).status == "pending"
    await store.decide(review.id, 123, approve_final)
    await runtime.execute(run.id, {"mode": "resume", "approval_id": str(review.id)})
    assert bool(list(settings.artifact_root.rglob("final.mp4"))) == approve_final
    assert (await store.get_run(run.id)).status == ("completed" if approve_final else "rejected")


async def test_approved_area_project_and_preferences(store, settings):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from lifeos.persistence.models import LifeArea, PlanningPreference

    area_id, project_id = uuid4(), uuid4()
    proposal = {
        "week_start": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        "summary": "Set up a personal project",
        "items": [],
        "new_life_areas": [{"id": str(area_id), "name": "Learning"}],
        "new_projects": [{"id": str(project_id), "life_area_id": str(area_id), "name": "Python"}],
        "new_tasks": [{"title": "Read a chapter", "project_id": str(project_id)}],
        "preference_updates": {
            "daily_minutes": {"0": 45},
            "timezone": "Asia/Kolkata",
            "notes": {"focus": "morning"},
        },
    }
    run = await store.submit(123, "planning", "Create learning project", {}, "source:setup")
    runtime = Runtime(settings, store, FakeGateway(planning=proposal), InMemorySaver())
    await runtime.execute(run.id, {"mode": "start"})
    assert await store.list_records("projects", 123) == []
    approval = await pending(store, run.id)
    await store.decide(approval.id, 123, True)
    await runtime.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})
    assert len(await store.list_records("projects", 123)) == 1
    assert (await store.list_records("tasks", 123))[0]["project_id"] == str(project_id)
    async with store.sessions() as session:
        assert (await session.get(LifeArea, area_id)).user_id == 123
        preference = await session.scalar(
            select(PlanningPreference).where(PlanningPreference.user_id == 123)
        )
        assert preference.daily_minutes["0"] == 45 and preference.preferences["focus"] == "morning"


async def test_stale_task_rejected_after_approval(store, settings):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    async with store.sessions.begin() as session:
        task = Task(id=uuid4(), user_id=123, title="Read", effort_minutes=30)
        session.add(task)
    proposal = {"week_start": today.isoformat(), "summary": "Read", "items": []}
    # Use a task update to exercise stale record protection on any weekday.
    proposal["task_updates"] = [{"task_id": str(task.id), "priority": 1}]
    run = await store.submit(123, "planning", "Update priority", {}, "source:stale")
    runtime = Runtime(settings, store, FakeGateway(planning=proposal), InMemorySaver())
    await runtime.execute(run.id, {"mode": "start"})
    approval = await pending(store, run.id)
    async with store.sessions.begin() as session:
        row = await session.get(Task, task.id)
        row.title = "Changed"
        row.updated_at = now() + timedelta(seconds=5)
    await store.decide(approval.id, 123, True)
    with pytest.raises(ValueError, match="changed while awaiting"):
        await runtime.execute(run.id, {"mode": "resume", "approval_id": str(approval.id)})


async def test_retry_after_approved_render_failure_resumes_checkpoint(store, settings, manifest):
    class RecoveringRenderer:
        def __init__(self):
            self.calls = 0

        async def render(self, manifest, images, voices, captions, destination):
            self.calls += 1
            if self.calls == 1:
                raise RenderError("Temporary render failure")
            destination.write_bytes(b"mock-review-file")

    settings.image_provider, settings.tts_provider = "mock", "mock"
    renderer = RecoveringRenderer()
    gateway = FakeGateway(manifest)
    run = await store.submit(123, "content", "Short", {}, "source:retry")
    runtime = Runtime(settings, store, gateway, InMemorySaver(), renderer=renderer)
    await runtime.execute(run.id, {"mode": "start"})
    approval = await pending(store, run.id, "storyboard")
    await store.decide(approval.id, 123, True)
    payload = {"mode": "resume", "approval_id": str(approval.id)}
    with pytest.raises(RenderError):
        await runtime.execute(run.id, payload)
    await runtime.execute(run.id, payload)
    assert renderer.calls == 2
    assert await pending(store, run.id, "final_review")
    assert gateway.calls == ["Idea", "Script", "ProductionManifest"]
