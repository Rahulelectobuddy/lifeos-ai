from collections import defaultdict
from datetime import date, timedelta
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanItem(StrictModel):
    task_id: UUID
    scheduled_date: date
    minutes: int = Field(ge=5, le=480)
    reason: str = Field(max_length=500)


class NewTask(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    project_id: UUID | None = None
    effort_minutes: int = Field(default=30, ge=5, le=1440)
    priority: int = Field(default=3, ge=1, le=5)
    deadline: date | None = None


class TaskUpdate(StrictModel):
    task_id: UUID
    title: str | None = Field(default=None, min_length=1, max_length=300)
    priority: int | None = Field(default=None, ge=1, le=5)
    effort_minutes: int | None = Field(default=None, ge=5, le=1440)
    deadline: date | None = None
    status: Literal["todo", "in_progress", "blocked", "done"] | None = None


class NewArea(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)


class NewProject(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    life_area_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)


class PreferenceUpdate(StrictModel):
    daily_minutes: dict[str, int] | None = None
    timezone: str | None = None
    notes: dict[str, str] = Field(default_factory=dict)

    @field_validator("daily_minutes")
    @classmethod
    def budgets(cls, value):
        if value is not None and any(
            key not in {str(i) for i in range(7)} or not 0 <= minutes <= 480
            for key, minutes in value.items()
        ):
            raise ValueError("Daily budgets must use weekday keys 0-6 and minutes 0-480")
        return value

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError("Unknown planning timezone") from error
        return value


class PlanProposal(StrictModel):
    week_start: date
    horizon_days: int = Field(default=7, ge=1, le=7)
    summary: str = Field(min_length=1, max_length=2000)
    items: list[PlanItem] = Field(max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    questions: list[str] = Field(default_factory=list, max_length=10)
    new_tasks: list[NewTask] = Field(default_factory=list, max_length=20)
    task_updates: list[TaskUpdate] = Field(default_factory=list, max_length=30)
    new_life_areas: list[NewArea] = Field(default_factory=list, max_length=10)
    new_projects: list[NewProject] = Field(default_factory=list, max_length=20)
    preference_updates: PreferenceUpdate = Field(default_factory=PreferenceUpdate)


def validate_plan(proposal: PlanProposal, context: dict) -> PlanProposal:
    tasks = {task["id"]: task.copy() for task in context["tasks"]}
    for update in proposal.task_updates:
        if str(update.task_id) not in tasks:
            raise ValueError("Unknown task update")
        tasks[str(update.task_id)].update(
            update.model_dump(mode="json", exclude={"task_id"}, exclude_none=True)
        )
    projects = {project["id"]: project for project in context["projects"]}
    by_day, scheduled = defaultdict(int), defaultdict(int)
    finish = {}
    dependencies = defaultdict(list)
    for dependency in context["dependencies"]:
        dependencies[dependency["task_id"]].append(dependency["depends_on_id"])
    last_date = None
    for position, item in enumerate(proposal.items):
        key = str(item.task_id)
        if key not in tasks:
            raise ValueError("Unknown task in plan")
        task = tasks[key]
        if task["status"] in {"blocked", "done"}:
            raise ValueError("Blocked or completed task cannot be scheduled")
        if task["project_id"] and projects.get(task["project_id"], {}).get("status") != "active":
            raise ValueError("Task is not in an active project")
        if (
            not proposal.week_start
            <= item.scheduled_date
            < proposal.week_start + timedelta(days=proposal.horizon_days)
        ):
            raise ValueError("Scheduled date lies outside plan horizon")
        if last_date and item.scheduled_date < last_date:
            raise ValueError("Plan items must be ordered chronologically")
        last_date = item.scheduled_date
        for dependency in dependencies[key]:
            if tasks.get(dependency, {}).get("status") != "done" and dependency not in finish:
                raise ValueError("Dependency must finish before dependent task starts")
        scheduled[key] += item.minutes
        if scheduled[key] > task["effort_minutes"]:
            raise ValueError("Scheduled effort exceeds task estimate")
        if scheduled[key] == task["effort_minutes"]:
            finish[key] = position
        by_day[item.scheduled_date] += item.minutes
    capacity = {
        **context["preferences"]["daily_minutes"],
        **(proposal.preference_updates.daily_minutes or {}),
    }
    for day, minutes in by_day.items():
        if minutes > capacity.get(str(day.weekday()), 0):
            raise ValueError("Plan exceeds available daily time")
    today = date.fromisoformat(context.get("today", date.today().isoformat()))
    warnings = list(proposal.warnings)
    for task in tasks.values():
        if (
            task.get("deadline")
            and date.fromisoformat(task["deadline"]) < today
            and task["status"] != "done"
        ):
            warnings.append(f"Overdue: {task['title']}")
        if task["status"] == "blocked":
            warnings.append(f"Blocked: {task['title']}")
        if task["project_id"] is None and task["status"] != "done":
            warnings.append(f"Missing project: {task['title']}")
        if task.get("deadline") and task["id"] in finish:
            last = max(
                item.scheduled_date for item in proposal.items if str(item.task_id) == task["id"]
            )
            if last > date.fromisoformat(task["deadline"]):
                warnings.append(f"Deadline conflict: {task['title']}")
    if any(
        task["status"] in {"todo", "in_progress"} and task["id"] not in finish
        for task in tasks.values()
    ):
        warnings.append(
            "Not all active tasks fit in the available time; unscheduled or partial tasks remain."
        )
    return proposal.model_copy(update={"warnings": list(dict.fromkeys(warnings))[:30]})
