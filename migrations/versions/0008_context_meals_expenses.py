"""Add grounded place context, planned meals, and reconciled expense details."""

import sqlalchemy as sa
from alembic import op

revision = "0008_context_meals_expenses"
down_revision = "0007_pricing_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "place_contexts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "place_id", sa.Integer(), sa.ForeignKey("places.id", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column("short_description", sa.Text(), nullable=False),
        sa.Column("why_visit", sa.JSON(), nullable=False),
        sa.Column("known_for", sa.JSON(), nullable=False),
        sa.Column("historical_significance", sa.Text(), nullable=True),
        sa.Column("cultural_significance", sa.Text(), nullable=True),
        sa.Column("architectural_significance", sa.Text(), nullable=True),
        sa.Column("famous_people", sa.JSON(), nullable=False),
        sa.Column("notable_events", sa.JSON(), nullable=False),
        sa.Column("movie_tv_connections", sa.JSON(), nullable=False),
        sa.Column("pop_culture_connections", sa.JSON(), nullable=False),
        sa.Column("local_tips", sa.JSON(), nullable=False),
        sa.Column("interesting_facts", sa.JSON(), nullable=False),
        sa.Column("best_experience", sa.Text(), nullable=True),
        sa.Column("recommended_visit_focus", sa.JSON(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_place_contexts_place_id", "place_contexts", ["place_id"])
    op.create_table(
        "place_context_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("context_id", sa.Integer(), sa.ForeignKey("place_contexts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_name", sa.String(160), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("claim_types", sa.JSON(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_place_context_sources_context_id", "place_context_sources", ["context_id"])
    op.create_table(
        "meals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("meal_type", sa.String(20), nullable=False),
        sa.Column("restaurant_place_id", sa.Integer(), sa.ForeignKey("places.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("traveler_count", sa.Integer(), nullable=False),
        sa.Column("min_cost_per_person", sa.Numeric(12, 2), nullable=False),
        sa.Column("expected_cost_per_person", sa.Numeric(12, 2), nullable=False),
        sa.Column("max_cost_per_person", sa.Numeric(12, 2), nullable=False),
        sa.Column("expected_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason_selected", sa.Text(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("source_quality", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "day_number", "meal_type"),
    )
    op.create_index("ix_meals_plan_id", "meals", ["plan_id"])
    op.create_index("ix_meals_day_number", "meals", ["day_number"])
    op.create_index("ix_meals_restaurant_place_id", "meals", ["restaurant_place_id"])
    op.create_table(
        "expense_line_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("subcategory", sa.String(60), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=True),
        sa.Column("itinerary_item_id", sa.String(160), nullable=True),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(60), nullable=False),
        sa.Column("unit_cost_min", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_cost_expected", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_cost_max", sa.Numeric(12, 2), nullable=False),
        sa.Column("subtotal_min", sa.Numeric(12, 2), nullable=False),
        sa.Column("subtotal_expected", sa.Numeric(12, 2), nullable=False),
        sa.Column("subtotal_max", sa.Numeric(12, 2), nullable=False),
        sa.Column("shared_or_personal", sa.String(20), nullable=False),
        sa.Column("traveler_count", sa.Integer(), nullable=False),
        sa.Column("taxes", sa.Numeric(12, 2), nullable=False),
        sa.Column("fees", sa.Numeric(12, 2), nullable=False),
        sa.Column("tips", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_min", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_expected", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_max", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("calculation_expression", sa.Text(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_reference", sa.String(1000), nullable=True),
        sa.Column("source_quality", sa.String(30), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_expense_line_items_plan_id", "expense_line_items", ["plan_id"])
    op.create_index("ix_expense_line_items_trip_id", "expense_line_items", ["trip_id"])
    op.create_index("ix_expense_line_items_category", "expense_line_items", ["category"])
    op.create_index("ix_expense_line_items_day_number", "expense_line_items", ["day_number"])
    op.create_table(
        "budget_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expected_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("category_totals", sa.JSON(), nullable=False),
        sa.Column("reconciliation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_budget_snapshots_plan_id", "budget_snapshots", ["plan_id"])


def downgrade() -> None:
    op.drop_table("budget_snapshots")
    op.drop_table("expense_line_items")
    op.drop_table("meals")
    op.drop_table("place_context_sources")
    op.drop_table("place_contexts")
