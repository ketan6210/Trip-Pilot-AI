"""Create Phase 1 trip, traveler, preference, and budget tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001_phase_one"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("destination_city", sa.String(100), nullable=False),
        sa.Column("origin", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("traveler_count", sa.Integer(), nullable=False),
        sa.Column("total_budget", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("pace", sa.String(20), nullable=False),
        sa.Column("transport_mode", sa.String(30), nullable=False),
        sa.Column("max_walking_per_day", sa.Integer(), nullable=False),
        sa.Column("hotel_style", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "travelers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("walking_tolerance", sa.Integer(), nullable=False),
        sa.Column("preferred_start_time", sa.Time(), nullable=False),
        sa.Column("preferred_end_time", sa.Time(), nullable=False),
        sa.Column("dietary_restrictions", sa.JSON(), nullable=False),
        sa.Column("accessibility_requirements", sa.JSON(), nullable=False),
    )
    op.create_index("ix_travelers_trip_id", "travelers", ["trip_id"])
    op.create_table(
        "traveler_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("traveler_id", sa.Integer(), sa.ForeignKey("travelers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("subcategory", sa.String(50), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("is_must_do", sa.Boolean(), nullable=False),
        sa.Column("is_avoid", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("traveler_id", "category", "subcategory"),
    )
    op.create_index("ix_traveler_preferences_traveler_id", "traveler_preferences", ["traveler_id"])
    op.create_table(
        "trip_budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("trip_budgets")
    op.drop_index("ix_traveler_preferences_traveler_id", table_name="traveler_preferences")
    op.drop_table("traveler_preferences")
    op.drop_index("ix_travelers_trip_id", table_name="travelers")
    op.drop_table("travelers")
    op.drop_table("trips")
