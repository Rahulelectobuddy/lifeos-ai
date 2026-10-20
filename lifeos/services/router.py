import json
from uuid import UUID

from lifeos.agents.content.schemas import ProductionManifest
from lifeos.integrations.zulip import parse_command

HELP = """LifeOS AI commands:
`/plan <daily/weekly request>` — propose a reviewed plan
`/tasks`, `/projects` — list your planning records
`/short <idea>` — Hindi transcript and storyboard
`/short revise <run UUID> <scene number> <instruction>` — propose a scene revision in a new run
`/short-status <run UUID>`, `/status <run UUID>` — view run status
`/approve <approval UUID>`, `/reject <approval UUID>` — decide your pending proposal
`/agents` — available agents
Media generation requires storyboard approval. Final MP4 export requires review approval.
No automatic publishing. Missing media providers produce a manifest, not fabricated media."""


class CommandRouter:
    def __init__(self, store):
        self.store = store

    async def dispatch(self, message):
        user_id, target = message["user_id"], message["target"]
        if user_id not in self.store.settings.allowed_user_ids:
            raise PermissionError("User not allowed")
        parsed = parse_command(message["content"])
        if parsed is None:
            return "Use /help for commands."
        command, arguments = parsed
        if command == "/help":
            return HELP
        if command == "/agents":
            return "Planning: daily/weekly plans and approved task changes. Content: Hindi Shorts scripts, scene revisions and optional media production."
        if command in {"/tasks", "/projects"}:
            rows = await self.store.list_records(command[1:], user_id)
            return (
                json.dumps(rows, ensure_ascii=False, indent=2)
                if rows
                else "No records yet. Use /plan to propose new tasks; they are saved only after approval."
            )
        if command in {"/status", "/short-status"}:
            run = await self.store.get_run(UUID(arguments), user_id)
            return f"Run `{run.id}`: {run.status}. Error: {run.error or 'none'}.\n" + json.dumps(
                run.output, ensure_ascii=False, indent=2
            )
        if command in {"/approve", "/reject"}:
            approval = await self.store.decide(UUID(arguments), user_id, command == "/approve")
            return f"Approval `{approval.id}` {approval.status}; run `{approval.run_id}` queued for resume."
        if command in {"/plan", "/short"}:
            data = {}
            if command == "/short" and arguments.startswith("revise "):
                parts = arguments.split(" ", 3)
                if len(parts) != 4:
                    raise ValueError("Usage: /short revise <run UUID> <scene> <instruction>")
                run = await self.store.get_run(UUID(parts[1]), user_id)
                if run.agent_type != "content":
                    raise ValueError("Revisions require a content run")
                manifest = run.output.get("manifest")
                if not manifest:
                    raise ValueError("Storyboard is not ready")
                manifest = ProductionManifest.model_validate(manifest)
                number = int(parts[2])
                if not 1 <= number <= len(manifest.scenes):
                    raise ValueError("Unknown scene")
                data = {
                    "base_manifest": manifest.model_dump(mode="json"),
                    "scene_number": number,
                    "parent_run_id": str(run.id),
                }
                arguments = parts[3]
            run = await self.store.submit(
                user_id,
                "planning" if command == "/plan" else "content",
                arguments,
                target,
                f"request:{message['source_id']}",
                data,
            )
            return (
                f"Queued {run.agent_type} run `{run.id}`. Progress and approvals will appear here."
            )
        raise ValueError("Unknown command")
