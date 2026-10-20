"""LifeOS-only tables, frozen initial schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# Explicit table definitions keep this migration independent of future ORM changes.
def upgrade():
    def table(name, *columns, constraints=()):
        op.create_table(
            name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            *columns,
            *constraints,
            schema="lifeos",
        )

    def c(name, type_, nullable=False):
        return sa.Column(name, type_, nullable=nullable)

    def fk(name, target, nullable=False):
        return sa.Column(
            name, sa.Uuid(), sa.ForeignKey("lifeos." + target + ".id"), nullable=nullable
        )

    table("life_areas", c("user_id", sa.Integer()), c("name", sa.String(200)))
    table(
        "projects",
        c("user_id", sa.Integer()),
        fk("life_area_id", "life_areas", True),
        c("name", sa.String(200)),
        c("status", sa.String(30)),
    )
    table(
        "tasks",
        c("user_id", sa.Integer()),
        fk("project_id", "projects", True),
        c("title", sa.String(300)),
        c("status", sa.String(30)),
        c("priority", sa.Integer()),
        c("effort_minutes", sa.Integer()),
        c("deadline", sa.Date(), True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    table(
        "task_dependencies",
        fk("task_id", "tasks"),
        fk("depends_on_id", "tasks"),
        constraints=(sa.UniqueConstraint("task_id", "depends_on_id"),),
    )
    table(
        "planning_preferences",
        c("user_id", sa.Integer()),
        c("daily_minutes", sa.JSON()),
        c("timezone", sa.String(80)),
        c("preferences", sa.JSON()),
        constraints=(sa.UniqueConstraint("user_id"),),
    )
    table(
        "agent_runs",
        c("user_id", sa.Integer()),
        c("agent_type", sa.String(30)),
        c("request", sa.Text()),
        c("status", sa.String(30)),
        c("model", sa.String(200)),
        c("started_at", sa.DateTime(timezone=True), True),
        c("ended_at", sa.DateTime(timezone=True), True),
        c("error", sa.Text(), True),
        c("reply_target", sa.JSON()),
        c("input_data", sa.JSON()),
        c("output", sa.JSON()),
    )
    table(
        "weekly_plans",
        c("user_id", sa.Integer()),
        fk("run_id", "agent_runs"),
        c("week_start", sa.Date()),
        c("summary", sa.Text()),
        c("preferences", sa.JSON()),
        constraints=(sa.UniqueConstraint("run_id"),),
    )
    table(
        "weekly_plan_items",
        fk("plan_id", "weekly_plans"),
        fk("task_id", "tasks"),
        c("scheduled_date", sa.Date()),
        c("minutes", sa.Integer()),
        c("position", sa.Integer()),
    )
    table(
        "content_projects",
        c("user_id", sa.Integer()),
        fk("run_id", "agent_runs"),
        c("title", sa.String(300)),
        c("manifest", sa.JSON()),
        c("status", sa.String(30)),
        constraints=(sa.UniqueConstraint("run_id"),),
    )
    table(
        "content_scenes",
        fk("project_id", "content_projects"),
        c("scene_number", sa.Integer()),
        c("data", sa.JSON()),
        constraints=(sa.UniqueConstraint("project_id", "scene_number"),),
    )
    table(
        "content_artifacts",
        fk("run_id", "agent_runs"),
        c("key", sa.String(500)),
        c("category", sa.String(40)),
        c("metadata_json", sa.JSON()),
        constraints=(sa.UniqueConstraint("run_id", "key"),),
    )
    table(
        "tool_executions",
        fk("run_id", "agent_runs"),
        c("name", sa.String(100)),
        c("classification", sa.String(10)),
        c("status", sa.String(30)),
        c("duration_ms", sa.Integer()),
        c("error", sa.String(300), True),
    )
    table(
        "approval_requests",
        fk("run_id", "agent_runs"),
        c("user_id", sa.Integer()),
        c("stage", sa.String(50)),
        c("status", sa.String(20)),
        c("payload", sa.JSON()),
        c("expires_at", sa.DateTime(timezone=True)),
        c("decided_at", sa.DateTime(timezone=True), True),
        c("decided_by", sa.Integer(), True),
        constraints=(sa.UniqueConstraint("run_id", "stage"),),
    )
    table(
        "workflow_events",
        fk("run_id", "agent_runs", True),
        c("kind", sa.String(50)),
        c("dedupe_key", sa.String(200)),
        c("payload", sa.JSON()),
        c("status", sa.String(30)),
        c("attempts", sa.Integer()),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        c("lease_until", sa.DateTime(timezone=True), True),
        c("error", sa.String(300), True),
        constraints=(sa.UniqueConstraint("dedupe_key"),),
    )
    for name in [
        "life_areas",
        "projects",
        "tasks",
        "agent_runs",
        "weekly_plans",
        "content_projects",
    ]:
        op.create_index(f"ix_{name}_user_id", name, ["user_id"], schema="lifeos")
    for name in ["agent_runs", "workflow_events"]:
        op.create_index(f"ix_{name}_status", name, ["status"], schema="lifeos")
    op.create_index("ix_workflow_events_kind", "workflow_events", ["kind"], schema="lifeos")
    for name in ["workflow_events", "approval_requests"]:
        op.create_index(f"ix_{name}_run_id", name, ["run_id"], schema="lifeos")


def downgrade():
    for name in [
        "workflow_events",
        "approval_requests",
        "tool_executions",
        "content_artifacts",
        "content_scenes",
        "content_projects",
        "weekly_plan_items",
        "weekly_plans",
        "agent_runs",
        "planning_preferences",
        "task_dependencies",
        "tasks",
        "projects",
        "life_areas",
    ]:
        op.drop_table(name, schema="lifeos")
