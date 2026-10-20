import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from lifeos.agents.planning.prompts import SYSTEM
from lifeos.agents.planning.schemas import PlanProposal, validate_plan
from lifeos.agents.planning.state import PlanningState
from lifeos.agents.planning.tools import load_context


def build_graph(gateway, registry, approvals, store, checkpointer):
    async def retrieve(state):
        context = await load_context(registry, state)
        context["today"] = (
            datetime.now(ZoneInfo(context["preferences"]["timezone"])).date().isoformat()
        )
        # A deliberately narrow parser adjusts explicit weekday budgets; all other
        # preference changes remain reviewed proposals, not model-selected limits.
        match = re.search(
            r"(\d{1,4})\s*minutes?\s+(?:every|each|per)\s+weekday", state["request"], re.I
        )
        if match:
            minutes = int(match.group(1))
            if not 5 <= minutes <= 480:
                raise ValueError("Weekday budget must be between 5 and 480 minutes")
            context["preferences"]["daily_minutes"] = {
                **context["preferences"]["daily_minutes"],
                **{str(day): minutes for day in range(5)},
            }
        return {"context": context}

    async def propose(state):
        request = json.dumps(
            {"request": state["request"], "context": state["context"]}, ensure_ascii=False
        )
        proposal = await gateway.structured(SYSTEM, request, PlanProposal)
        if proposal.week_start.isoformat() < state["context"]["today"]:
            raise ValueError("Cannot plan in the past")
        validated = validate_plan(proposal, state["context"])
        return {"proposal": validated.model_dump(mode="json")}

    async def approval(state):
        return {"approved": await approvals.wait(state, "planning", state["proposal"])}

    async def persist(state):
        plan_id = await store.save_plan(state, state["proposal"])
        return {"plan_id": plan_id, "status": "completed"}

    async def reject(_state):
        return {"status": "rejected"}

    graph = StateGraph(PlanningState)
    for name, node in [
        ("retrieve", retrieve),
        ("propose", propose),
        ("approval", approval),
        ("persist", persist),
        ("reject", reject),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "propose")
    graph.add_edge("propose", "approval")
    graph.add_conditional_edges(
        "approval", lambda state: "persist" if state["approved"] else "reject"
    )
    graph.add_edge("persist", END)
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=checkpointer)
