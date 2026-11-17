"""Development application credentials and sessions; revision 0005."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _common():
    return [
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
    ]


def _create(name, *columns, unique_principal=False):
    constraints = [
        sa.PrimaryKeyConstraint("id", name=f"pk_{name}"),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
        sa.ForeignKeyConstraint(["tenant_id", "principal_id"], ["principal.tenant_id", "principal.id"], name=f"fk_{name}_tenant_id_principal_id_principal"),
    ]
    if unique_principal:
        constraints.append(sa.UniqueConstraint("tenant_id", "principal_id", name=f"uq_{name}_tenant_id_principal_id"))
    op.create_table(name, sa.Column("principal_id", sa.UUID(), nullable=False), *columns, *_common(), *constraints)


def upgrade():
    _create("local_credential", sa.Column("password_hash", sa.Text(), nullable=False), unique_principal=True)
    _create("local_session", sa.Column("token_hash", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_local_session_token_hash", "local_session", ["token_hash"])


def downgrade():
    op.drop_table("local_session")
    op.drop_table("local_credential")
