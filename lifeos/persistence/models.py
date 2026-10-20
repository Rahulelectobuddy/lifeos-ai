from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(schema="lifeos")


class Record:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LifeArea(Record, Base):
    __tablename__ = "life_areas"
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200))


class Project(Record, Base):
    __tablename__ = "projects"
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    life_area_id: Mapped[UUID | None] = mapped_column(ForeignKey("lifeos.life_areas.id"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="active")


class Task(Record, Base):
    __tablename__ = "tasks"
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("lifeos.projects.id"))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="todo")
    priority: Mapped[int] = mapped_column(Integer, default=3)
    effort_minutes: Mapped[int] = mapped_column(Integer, default=30)
    deadline: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TaskDependency(Record, Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_id"),)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.tasks.id"))
    depends_on_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.tasks.id"))


class PlanningPreference(Record, Base):
    __tablename__ = "planning_preferences"
    __table_args__ = (UniqueConstraint("user_id"),)
    user_id: Mapped[int] = mapped_column(Integer)
    daily_minutes: Mapped[dict[str, int]] = mapped_column(
        JSON, default=lambda: {str(i): 90 if i < 5 else 0 for i in range(7)}
    )
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Kolkata")
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AgentRun(Record, Base):
    __tablename__ = "agent_runs"
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    agent_type: Mapped[str] = mapped_column(String(30))
    request: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    model: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    reply_target: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WeeklyPlan(Record, Base):
    __tablename__ = "weekly_plans"
    __table_args__ = (UniqueConstraint("run_id"),)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.agent_runs.id"))
    week_start: Mapped[date] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WeeklyPlanItem(Record, Base):
    __tablename__ = "weekly_plan_items"
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.weekly_plans.id"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.tasks.id"))
    scheduled_date: Mapped[date] = mapped_column(Date)
    minutes: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)


class ContentProject(Record, Base):
    __tablename__ = "content_projects"
    __table_args__ = (UniqueConstraint("run_id"),)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.agent_runs.id"))
    title: Mapped[str] = mapped_column(String(300))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="draft")


class ContentScene(Record, Base):
    __tablename__ = "content_scenes"
    __table_args__ = (UniqueConstraint("project_id", "scene_number"),)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.content_projects.id"))
    scene_number: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class ContentArtifact(Record, Base):
    __tablename__ = "content_artifacts"
    __table_args__ = (UniqueConstraint("run_id", "key"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.agent_runs.id"))
    key: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ToolExecution(Record, Base):
    __tablename__ = "tool_executions"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.agent_runs.id"))
    name: Mapped[str] = mapped_column(String(100))
    classification: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(300))


class ApprovalRequest(Record, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (UniqueConstraint("run_id", "stage"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("lifeos.agent_runs.id"), index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column(Integer)


class WorkflowEvent(Record, Base):
    __tablename__ = "workflow_events"
    __table_args__ = (UniqueConstraint("dedupe_key"),)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("lifeos.agent_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(300))
