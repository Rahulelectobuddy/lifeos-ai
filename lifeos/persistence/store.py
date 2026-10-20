from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from lifeos.core.approvals import ApprovalError, validate_decision
from lifeos.persistence.models import (
    AgentRun,
    ApprovalRequest,
    ContentArtifact,
    ContentProject,
    ContentScene,
    LifeArea,
    PlanningPreference,
    Project,
    Task,
    TaskDependency,
    ToolExecution,
    WeeklyPlan,
    WeeklyPlanItem,
    WorkflowEvent,
)


def now():
    return datetime.now(UTC)


def serialize(record, names):
    return {
        name: str(value)
        if isinstance(value := getattr(record, name), UUID)
        else value.isoformat()
        if hasattr(value, "isoformat")
        else value
        for name in names
    }


class Store:
    def __init__(self, sessions, settings):
        self.sessions = sessions
        self.settings = settings

    async def event(self, session, kind, key, payload, run_id=None, status="queued"):
        insert = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
        statement = insert(WorkflowEvent).values(
            id=uuid4(),
            kind=kind,
            dedupe_key=key,
            payload=payload,
            run_id=run_id,
            status=status,
            attempts=0,
            available_at=now(),
        )
        result = await session.execute(
            statement.on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(
                WorkflowEvent.id
            )
        )
        return result.scalar_one_or_none()

    async def enqueue(self, kind, key, payload, run_id=None):
        async with self.sessions.begin() as session:
            return await self.event(session, kind, key, payload, run_id)

    async def submit(self, user_id, agent_type, request, reply_target, source_key, input_data=None):
        if user_id not in self.settings.allowed_user_ids:
            raise PermissionError("User not allowed")
        if agent_type not in {"planning", "content"}:
            raise ValueError("Unknown agent")
        if not request.strip() or len(request) > self.settings.max_request_chars:
            raise ValueError("Invalid request length")
        async with self.sessions.begin() as session:
            # PostgreSQL transaction advisory lock serializes per-user budget checks.
            if session.bind.dialect.name == "postgresql":
                await session.execute(select(func.pg_advisory_xact_lock(user_id)))
            previous = await session.scalar(
                select(WorkflowEvent).where(WorkflowEvent.dedupe_key == source_key)
            )
            if previous:
                run = await session.get(AgentRun, previous.run_id)
                if not run or run.user_id != user_id:
                    raise PermissionError("Idempotency key ownership mismatch")
                return run
            active = await session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(
                    AgentRun.user_id == user_id,
                    AgentRun.status.in_(["queued", "running", "waiting_approval"]),
                )
            )
            recent = await session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(
                    AgentRun.user_id == user_id, AgentRun.created_at >= now() - timedelta(minutes=1)
                )
            )
            if (
                active >= self.settings.max_active_runs_per_user
                or recent >= self.settings.rate_limit_per_minute
            ):
                raise ValueError("Execution budget exceeded")
            run = AgentRun(
                id=uuid4(),
                user_id=user_id,
                agent_type=agent_type,
                request=request,
                model=self.settings.dgx_model,
                reply_target=reply_target,
                input_data=input_data or {},
            )
            session.add(run)
            await session.flush()
            await self.event(session, "run", source_key, {"mode": "start"}, run.id)
            return run

    async def get_run(self, run_id, user_id=None):
        async with self.sessions() as session:
            run = await session.get(AgentRun, run_id)
            if not run or (user_id is not None and run.user_id != user_id):
                raise ValueError("Run not found")
            return run

    async def list_records(self, kind, user_id):
        model = {"tasks": Task, "projects": Project}[kind]
        fields = (
            ["id", "name", "status"]
            if kind == "projects"
            else [
                "id",
                "title",
                "status",
                "priority",
                "effort_minutes",
                "deadline",
                "project_id",
                "updated_at",
            ]
        )
        async with self.sessions() as session:
            return [
                serialize(row, fields)
                for row in (
                    await session.scalars(select(model).where(model.user_id == user_id).limit(500))
                ).all()
            ]

    async def planning_context(self, user_id):
        async with self.sessions() as session:
            tasks = (
                await session.scalars(select(Task).where(Task.user_id == user_id).limit(500))
            ).all()
            projects = (
                await session.scalars(select(Project).where(Project.user_id == user_id))
            ).all()
            preference = await session.scalar(
                select(PlanningPreference).where(PlanningPreference.user_id == user_id)
            )
            areas = (
                await session.scalars(select(LifeArea).where(LifeArea.user_id == user_id))
            ).all()
            ids = [task.id for task in tasks]
            dependencies = (
                (
                    await session.scalars(
                        select(TaskDependency).where(
                            TaskDependency.task_id.in_(ids), TaskDependency.depends_on_id.in_(ids)
                        )
                    )
                ).all()
                if ids
                else []
            )
            return {
                "life_areas": [serialize(area, ["id", "name"]) for area in areas],
                "tasks": [
                    serialize(
                        task,
                        [
                            "id",
                            "project_id",
                            "title",
                            "status",
                            "priority",
                            "effort_minutes",
                            "deadline",
                            "updated_at",
                        ],
                    )
                    for task in tasks
                ],
                "projects": [serialize(project, ["id", "name", "status"]) for project in projects],
                "dependencies": [
                    {"task_id": str(d.task_id), "depends_on_id": str(d.depends_on_id)}
                    for d in dependencies
                ],
                "preferences": {
                    "daily_minutes": preference.daily_minutes,
                    "timezone": preference.timezone,
                    "preferences": preference.preferences,
                }
                if preference
                else {
                    "daily_minutes": {str(i): 90 if i < 5 else 0 for i in range(7)},
                    "timezone": "Asia/Kolkata",
                    "preferences": {},
                },
            }

    async def ensure_approval(self, run_id, user_id, stage, payload):
        async with self.sessions.begin() as session:
            run = await session.get(AgentRun, run_id, with_for_update=True)
            if not run or run.user_id != user_id:
                raise PermissionError("Approval ownership mismatch")
            existing = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == run_id, ApprovalRequest.stage == stage
                )
            )
            if existing:
                if existing.payload != payload:
                    raise ApprovalError("Proposal changed after approval was requested")
                return existing
            approval = ApprovalRequest(
                id=uuid4(),
                run_id=run_id,
                user_id=user_id,
                stage=stage,
                payload=payload,
                expires_at=now() + timedelta(hours=self.settings.approval_ttl_hours),
            )
            session.add(approval)
            run.status = "waiting_approval"
            run.output = payload
            await session.flush()
            await self.event(
                session,
                "notify",
                f"approval:{approval.id}",
                {"target": run.reply_target, "text": self.approval_message(approval)},
                run_id,
            )
            return approval

    @staticmethod
    def approval_message(approval):
        import json

        payload = json.dumps(approval.payload, ensure_ascii=False, indent=2)
        return f"Review {approval.stage} for run `{approval.run_id}`\n```json\n{payload}\n```\nApprove: `/approve {approval.id}`\nReject: `/reject {approval.id}`\nExpires: {approval.expires_at.isoformat()}"

    async def get_approval(self, approval_id):
        async with self.sessions() as session:
            result = await session.get(ApprovalRequest, approval_id)
            if not result:
                raise ValueError("Approval not found")
            return result

    async def decide(self, approval_id, user_id, approve):
        if user_id not in self.settings.allowed_user_ids:
            raise PermissionError("User not allowed")
        async with self.sessions.begin() as session:
            approval = await session.get(ApprovalRequest, approval_id, with_for_update=True)
            if not approval:
                raise ValueError("Approval not found")
            validate_decision(approval, user_id, now())
            approval.status = "approved" if approve else "rejected"
            approval.decided_by, approval.decided_at = user_id, now()
            await self.event(
                session,
                "audit",
                f"decision:{approval.id}",
                {"user_id": user_id, "decision": approval.status},
                approval.run_id,
                "done",
            )
            await self.event(
                session,
                "run",
                f"resume:{approval.id}",
                {"mode": "resume", "approval_id": str(approval.id)},
                approval.run_id,
            )
            return approval

    async def expire_approvals(self):
        async with self.sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(ApprovalRequest)
                    .where(ApprovalRequest.status == "pending", ApprovalRequest.expires_at <= now())
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for approval in rows:
                approval.status, approval.decided_at = "expired", now()
                await self.event(
                    session,
                    "run",
                    f"expire:{approval.id}",
                    {"mode": "resume", "approval_id": str(approval.id)},
                    approval.run_id,
                )
                await self.event(
                    session,
                    "audit",
                    f"expiration:{approval.id}",
                    {"decision": "expired"},
                    approval.run_id,
                    "done",
                )

    async def save_plan(self, state, proposal):
        from lifeos.agents.planning.schemas import PlanProposal, validate_plan

        proposal = PlanProposal.model_validate(proposal)
        context = await self.planning_context(state["user_id"])
        context["preferences"]["daily_minutes"] = state["context"]["preferences"]["daily_minutes"]
        validate_plan(proposal, context)
        run_id = UUID(state["run_id"])
        async with self.sessions.begin() as session:
            run = await session.get(AgentRun, run_id, with_for_update=True)
            previous = await session.scalar(select(WeeklyPlan).where(WeeklyPlan.run_id == run_id))
            if previous:
                return str(previous.id)
            approval = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == run_id, ApprovalRequest.stage == "planning"
                )
            )
            if (
                not approval
                or approval.status != "approved"
                or approval.user_id != state["user_id"]
            ):
                raise PermissionError("Verified planning approval required")
            # Reject stale tasks before applying the reviewed proposal.
            snapshot = {task["id"]: task for task in state["context"]["tasks"]}
            for task_id in {str(item.task_id) for item in proposal.items} | {
                str(update.task_id) for update in proposal.task_updates
            }:
                task = await session.get(Task, UUID(task_id), with_for_update=True)
                if not task or task.user_id != run.user_id:
                    raise PermissionError("Task ownership changed")
                if serialize(task, ["updated_at"])["updated_at"] != snapshot[task_id]["updated_at"]:
                    raise ValueError("Task changed while awaiting approval; request a new plan")
            for update in proposal.task_updates:
                task = await session.get(Task, update.task_id)
                for key, value in update.model_dump(exclude={"task_id"}, exclude_none=True).items():
                    setattr(task, key, value)
            for area in proposal.new_life_areas:
                if await session.get(LifeArea, area.id):
                    raise ValueError("New life area ID already exists")
                session.add(LifeArea(id=area.id, user_id=run.user_id, name=area.name))
            await session.flush()
            for draft_project in proposal.new_projects:
                if await session.get(Project, draft_project.id):
                    raise ValueError("New project ID already exists")
                if draft_project.life_area_id:
                    area = await session.get(LifeArea, draft_project.life_area_id)
                    if not area or area.user_id != run.user_id:
                        raise PermissionError("Life area ownership mismatch")
                session.add(Project(user_id=run.user_id, **draft_project.model_dump()))
            await session.flush()
            for draft in proposal.new_tasks:
                if draft.project_id:
                    project = await session.get(Project, draft.project_id)
                    if not project or project.user_id != run.user_id:
                        raise PermissionError("Unknown project")
                session.add(Task(user_id=run.user_id, **draft.model_dump()))
            plan = WeeklyPlan(
                id=uuid4(),
                user_id=run.user_id,
                run_id=run_id,
                week_start=proposal.week_start,
                summary=proposal.summary,
                preferences=proposal.preference_updates.model_dump(mode="json", exclude_none=True),
            )
            session.add(plan)
            await session.flush()
            for position, item in enumerate(proposal.items):
                session.add(
                    WeeklyPlanItem(
                        plan_id=plan.id,
                        task_id=item.task_id,
                        scheduled_date=item.scheduled_date,
                        minutes=item.minutes,
                        position=position,
                    )
                )
            patch = proposal.preference_updates
            if patch.daily_minutes is not None or patch.timezone or patch.notes:
                preference = await session.scalar(
                    select(PlanningPreference).where(PlanningPreference.user_id == run.user_id)
                )
                if not preference:
                    preference = PlanningPreference(user_id=run.user_id)
                    session.add(preference)
                if patch.daily_minutes is not None:
                    preference.daily_minutes = {
                        **(preference.daily_minutes or context["preferences"]["daily_minutes"]),
                        **patch.daily_minutes,
                    }
                if patch.timezone:
                    preference.timezone = patch.timezone
                preference.preferences = {**(preference.preferences or {}), **patch.notes}
            if "planning_reminder" in self.settings.n8n_workflows:
                await self.event(
                    session,
                    "n8n",
                    f"plan-reminder:{run_id}",
                    {
                        "workflow": "planning_reminder",
                        "data": {
                            "plan_id": str(plan.id),
                            "user_id": run.user_id,
                            "proposal": proposal.model_dump(mode="json"),
                        },
                    },
                    run_id,
                )
            return str(plan.id)

    async def content_metadata(self, state):
        run_id = UUID(state["run_id"])
        async with self.sessions.begin() as session:
            project = await session.scalar(
                select(ContentProject).where(ContentProject.run_id == run_id).with_for_update()
            )
            manifest = state["manifest"]
            if not project:
                project = ContentProject(
                    id=uuid4(),
                    run_id=run_id,
                    user_id=state["user_id"],
                    title=manifest["title"],
                    manifest=manifest,
                )
                session.add(project)
                await session.flush()
                for scene in manifest["scenes"]:
                    session.add(
                        ContentScene(
                            project_id=project.id, scene_number=scene["number"], data=scene
                        )
                    )
            project.manifest = manifest
            project.status = state.get("production_status", "draft")
            return str(project.id)

    async def save_artifact(self, run_id, key, category, metadata):
        async with self.sessions.begin() as session:
            insert = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
            await session.execute(
                insert(ContentArtifact)
                .values(
                    id=uuid4(), run_id=run_id, key=key, category=category, metadata_json=metadata
                )
                .on_conflict_do_nothing(index_elements=["run_id", "key"])
            )

    async def artifacts(self, run_id, user_id):
        await self.get_run(run_id, user_id)
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        select(ContentArtifact).where(ContentArtifact.run_id == run_id)
                    )
                ).all()
            )

    async def audit_tool(self, run_id, name, classification, status, duration_ms, error):
        async with self.sessions.begin() as session:
            session.add(
                ToolExecution(
                    run_id=run_id,
                    name=name,
                    classification=classification,
                    status=status,
                    duration_ms=duration_ms,
                    error=error,
                )
            )

    async def set_run(self, run_id, status, output=None, error=None):
        async with self.sessions.begin() as session:
            run = await session.get(AgentRun, run_id, with_for_update=True)
            run.status = status
            if status == "running" and not run.started_at:
                run.started_at = now()
            if status in {"completed", "failed", "rejected", "manifest_only"}:
                run.ended_at = now()
            if output is not None:
                run.output = output
            run.error = error

    async def claim(self, kinds):
        async with self.sessions.begin() as session:
            job = await session.scalar(
                select(WorkflowEvent)
                .where(
                    WorkflowEvent.kind.in_(kinds),
                    or_(
                        and_(WorkflowEvent.status == "queued", WorkflowEvent.available_at <= now()),
                        and_(
                            WorkflowEvent.status == "processing", WorkflowEvent.lease_until < now()
                        ),
                    ),
                )
                .order_by(WorkflowEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not job:
                return None
            job.status = "processing"
            job.attempts += 1
            job.lease_until = now() + timedelta(seconds=self.settings.run_timeout_seconds + 60)
            return job

    async def finish_job(self, job_id, error=None, retry=True):
        async with self.sessions.begin() as session:
            job = await session.get(WorkflowEvent, job_id, with_for_update=True)
            if error and retry and job.attempts < 3:
                job.status = "queued"
                job.available_at = now() + timedelta(seconds=min(60, 2**job.attempts))
            else:
                job.status = "failed" if error else "done"
            job.error, job.lease_until = error, None
            return job.status

    async def notify(self, run, text, key):
        await self.enqueue(
            "notify", f"notify:{run.id}:{key}", {"target": run.reply_target, "text": text}, run.id
        )
