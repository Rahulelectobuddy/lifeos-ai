from typing import Any, TypedDict


class PlanningState(TypedDict, total=False):
    run_id: str
    user_id: int
    request: str
    context: dict[str, Any]
    proposal: dict[str, Any]
    approved: bool
    plan_id: str
    status: str
