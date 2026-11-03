"""Add durable TripState and orchestration messages."""

import sqlalchemy as sa
from alembic import op

revision = "0004_orchestration"
down_revision = "0003_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trip_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, unique=True),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "orchestration_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("trip_states.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_command", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_orchestration_messages_session_id", "orchestration_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_orchestration_messages_session_id", table_name="orchestration_messages")
    op.drop_table("orchestration_messages")
    op.drop_table("trip_states")
