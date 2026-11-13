"""business_event is append-only

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION business_event_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'business_event is append-only (% rejected)', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER business_event_no_update_delete
        BEFORE UPDATE OR DELETE ON business_event
        FOR EACH ROW EXECUTE FUNCTION business_event_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER business_event_no_update_delete ON business_event")
    op.execute("DROP FUNCTION business_event_append_only()")
