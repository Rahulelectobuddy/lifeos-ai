from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from lifeos.agents.planning.schemas import PlanProposal, PreferenceUpdate, validate_plan


def test_invalid_timezone_is_a_validation_error():
    with pytest.raises(ValidationError, match="Unknown planning timezone"):
        PreferenceUpdate(timezone="Invalid/Timezone")


def fixture_context(status="todo", effort=30):
    monday = date(2026, 10, 19)
    task_id, project_id = str(uuid4()), str(uuid4())
    return (
        monday,
        task_id,
        {
            "today": monday.isoformat(),
            "tasks": [
                {
                    "id": task_id,
                    "project_id": project_id,
                    "title": "Read",
                    "status": status,
                    "priority": 1,
                    "effort_minutes": effort,
                    "deadline": (monday - timedelta(days=1)).isoformat(),
                }
            ],
            "projects": [{"id": project_id, "status": "active"}],
            "dependencies": [],
            "preferences": {"daily_minutes": {"0": 90}},
        },
    )


def test_overdue_and_missing_project():
    day, task_id, context = fixture_context()
    context["tasks"][0]["project_id"] = None
    proposal = PlanProposal(
        week_start=day,
        summary="Plan",
        items=[{"task_id": task_id, "scheduled_date": day, "minutes": 30, "reason": "Overdue"}],
    )
    result = validate_plan(proposal, context)
    assert any("Overdue" in warning for warning in result.warnings)
    assert any("Missing project" in warning for warning in result.warnings)
    assert any("Deadline conflict" in warning for warning in result.warnings)


@pytest.mark.parametrize("status", ["blocked", "done"])
def test_blocked_tasks(status):
    day, task_id, context = fixture_context(status)
    with pytest.raises(ValueError, match="Blocked or completed"):
        validate_plan(
            PlanProposal(
                week_start=day,
                summary="Plan",
                items=[
                    {"task_id": task_id, "scheduled_date": day, "minutes": 30, "reason": "Do it"}
                ],
            ),
            context,
        )


def test_insufficient_time():
    day, task_id, context = fixture_context(effort=120)
    with pytest.raises(ValueError, match="daily time"):
        validate_plan(
            PlanProposal(
                week_start=day,
                summary="Plan",
                items=[
                    {"task_id": task_id, "scheduled_date": day, "minutes": 120, "reason": "Do it"}
                ],
            ),
            context,
        )


def test_dependency_order():
    day, task_id, context = fixture_context()
    dependency = str(uuid4())
    context["tasks"].append({**context["tasks"][0], "id": dependency, "title": "First"})
    context["dependencies"] = [{"task_id": task_id, "depends_on_id": dependency}]

    def item(key):
        return {"task_id": key, "scheduled_date": day, "minutes": 30, "reason": "Do it"}

    with pytest.raises(ValueError, match="Dependency"):
        validate_plan(
            PlanProposal(week_start=day, summary="Plan", items=[item(task_id), item(dependency)]),
            context,
        )
    assert validate_plan(
        PlanProposal(week_start=day, summary="Plan", items=[item(dependency), item(task_id)]),
        context,
    )


def test_partial_plan_warns():
    day, task_id, context = fixture_context()
    result = validate_plan(PlanProposal(week_start=day, summary="Plan", items=[]), context)
    assert any("available time" in warning for warning in result.warnings)
