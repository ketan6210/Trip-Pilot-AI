"""Add persisted selected-plan detail documents."""

import sqlalchemy as sa
from alembic import op

revision = "0005_plan_detail"
down_revision = "0004_orchestration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_details",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "plan_id", sa.Integer(), sa.ForeignKey("plan_variants.id", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plan_details_plan_id", "plan_details", ["plan_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_plan_details_plan_id", table_name="plan_details")
    op.drop_table("plan_details")
