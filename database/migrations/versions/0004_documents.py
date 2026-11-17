"""document_version and document_chunk

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "document_version",
        sa.Column("document_key", sa.Text(), nullable=False),
        sa.Column("revision", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("object_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "applicable_models", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column("doc_kind", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extraction_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'ACTIVE'"), nullable=False),
        *_common(),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE', 'WITHDRAWN')", name=op.f("ck_document_version_status_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_version")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_document_version_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "document_key", "revision", "content_hash",
            name=op.f("uq_document_version_tenant_id_document_key_revision_content_hash"),
        ),
    )
    op.create_table(
        "document_chunk",
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("table_header", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        *_common(),
        sa.CheckConstraint("kind IN ('TEXT', 'TABLE')", name=op.f("ck_document_chunk_kind_valid")),
        sa.CheckConstraint("page_no >= 1", name=op.f("ck_document_chunk_page_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"], ["document_version.tenant_id", "document_version.id"],
            name=op.f("fk_document_chunk_tenant_id_document_version_id_document_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunk")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_document_chunk_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "document_version_id", "page_no", "ordinal",
            name=op.f("uq_document_chunk_tenant_id_document_version_id_page_no_ordinal"),
        ),
    )


def downgrade() -> None:
    op.drop_table("document_chunk")
    op.drop_table("document_version")
