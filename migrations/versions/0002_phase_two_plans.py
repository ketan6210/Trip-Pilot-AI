"""Add strategy plans and group voting."""

import sqlalchemy as sa
from alembic import op

revision = "0002_phase_two"
down_revision = "0001_phase_one"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("estimated_total_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("estimated_per_person_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("mean_group_satisfaction", sa.Float(), nullable=False),
        sa.Column("minimum_traveler_satisfaction", sa.Float(), nullable=False),
        sa.Column("satisfaction_variance", sa.Float(), nullable=False),
        sa.Column("fairness_score", sa.Float(), nullable=False),
        sa.Column("travel_minutes", sa.Integer(), nullable=False),
        sa.Column("walking_intensity", sa.String(20), nullable=False),
        sa.Column("activity_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plan_variants_trip_id", "plan_variants", ["trip_id"])
    op.create_table(
        "votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "plan_variant_id", sa.Integer(), sa.ForeignKey("plan_variants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("traveler_id", sa.Integer(), sa.ForeignKey("travelers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vote_type", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_variant_id", "traveler_id", "vote_type"),
    )
    op.create_index("ix_votes_trip_id", "votes", ["trip_id"])
    op.create_index("ix_votes_plan_variant_id", "votes", ["plan_variant_id"])
    op.create_index("ix_votes_traveler_id", "votes", ["traveler_id"])


def downgrade() -> None:
    op.drop_index("ix_votes_traveler_id", table_name="votes")
    op.drop_index("ix_votes_plan_variant_id", table_name="votes")
    op.drop_index("ix_votes_trip_id", table_name="votes")
    op.drop_table("votes")
    op.drop_index("ix_plan_variants_trip_id", table_name="plan_variants")
    op.drop_table("plan_variants")
