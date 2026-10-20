from datetime import UTC, datetime
from uuid import UUID

from langgraph.types import interrupt

from lifeos.core.observability import latency


class ApprovalError(ValueError):
    pass


def validate_decision(approval, user_id: int, now: datetime):
    if approval.user_id != user_id:
        raise PermissionError("Only the requesting user can decide this approval")
    expires = approval.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= now:
        raise ApprovalError("Approval expired; submit a new request")
    if approval.status != "pending":
        raise ApprovalError("Approval already decided")


class ApprovalEngine:
    def __init__(self, store):
        self.store = store

    async def wait(self, state, stage: str, payload: dict) -> bool:
        approval = await self.store.ensure_approval(
            UUID(state["run_id"]), state["user_id"], stage, payload
        )
        # Always re-enter the interrupt on resume. Side effects before it are idempotent.
        decision = interrupt({"approval_id": str(approval.id), "stage": stage, "proposal": payload})
        if not isinstance(decision, dict) or decision.get("approval_id") != str(approval.id):
            raise ApprovalError("Resume token does not match this approval")
        verified = await self.store.get_approval(approval.id)
        if verified.status not in {"approved", "rejected", "expired"}:
            raise ApprovalError("Approval must be decided through the authorized service")
        if (verified.status != "expired" and verified.decided_by != state["user_id"]) or (
            verified.status == "expired" and verified.decided_by is not None
        ):
            raise PermissionError("Invalid decision identity")
        latency.record(
            max(0, (datetime.now(UTC) - verified.created_at.replace(tzinfo=UTC)).total_seconds()),
            {"operation": "approval.wait"},
        )
        return verified.status == "approved"
