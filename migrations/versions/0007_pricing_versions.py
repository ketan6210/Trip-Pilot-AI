"""Add typed cost ledgers and trip/plan versioning."""

import sqlalchemy as sa
from alembic import op

revision = "0007_pricing_versions"
down_revision = "0006_live_places"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("flight_included", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("trips", sa.Column("trip_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("plan_variants", sa.Column("plan_set_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "plan_cost_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cost_id", sa.String(120), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("item_id", sa.String(160), nullable=True),
        sa.Column("minimum", sa.Numeric(12, 2), nullable=True),
        sa.Column("expected", sa.Numeric(12, 2), nullable=True),
        sa.Column("maximum", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("shared_or_personal", sa.String(20), nullable=False),
        sa.Column("price_type", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provider", sa.String(120), nullable=True),
        sa.Column("source", sa.String(1000), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    op.create_index("ix_plan_cost_entries_plan_id", "plan_cost_entries", ["plan_id"])
    op.create_index("ix_plan_cost_entries_cost_id", "plan_cost_entries", ["cost_id"])
    op.create_index("ix_plan_cost_entries_category", "plan_cost_entries", ["category"])


def downgrade() -> None:
    op.drop_index("ix_plan_cost_entries_category", table_name="plan_cost_entries")
    op.drop_index("ix_plan_cost_entries_cost_id", table_name="plan_cost_entries")
    op.drop_index("ix_plan_cost_entries_plan_id", table_name="plan_cost_entries")
    op.drop_table("plan_cost_entries")
    op.drop_column("plan_variants", "plan_set_version")
    op.drop_column("trips", "trip_version")
    op.drop_column("trips", "flight_included")
