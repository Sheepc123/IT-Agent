"""task_run for analysis results

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_run",
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("request_revision_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("input_kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("model_call_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("call_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('WORKFLOW', 'FIXED_RAG', 'AGENT')", name=op.f("ck_task_run_mode_valid")),
        sa.CheckConstraint(
            "input_kind IN ('FORM', 'NATURAL_LANGUAGE')", name=op.f("ck_task_run_input_kind_valid")
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'WAITING_INPUT', 'SUCCEEDED', 'BUDGET_EXHAUSTED', 'CANCELLED', 'FAILED')",
            name=op.f("ck_task_run_status_valid"),
        ),
        sa.CheckConstraint("model_call_count >= 0", name=op.f("ck_task_run_model_call_count_non_negative")),
        sa.CheckConstraint("call_count >= 0", name=op.f("ck_task_run_call_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"], ["purchase_request.tenant_id", "purchase_request.id"],
            name=op.f("fk_task_run_tenant_id_request_id_purchase_request"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_revision_id"], ["request_revision.tenant_id", "request_revision.id"],
            name=op.f("fk_task_run_tenant_id_request_revision_id_request_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"], ["principal.tenant_id", "principal.id"],
            name=op.f("fk_task_run_tenant_id_actor_id_principal"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_run")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_task_run_tenant_id_id")),
    )


def downgrade() -> None:
    op.drop_table("task_run")
