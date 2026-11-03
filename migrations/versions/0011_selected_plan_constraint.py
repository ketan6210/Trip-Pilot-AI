"""Enforce a single selected itinerary per trip under concurrent requests."""

import sqlalchemy as sa
from alembic import op

revision = "0011_selected_plan"
down_revision = "0010_security_itineraries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Resolve only duplicate selections left by old unauthenticated race conditions.
    op.execute("""UPDATE plan_variants SET status='candidate'
        WHERE status='selected' AND id NOT IN
        (SELECT MAX(id) FROM plan_variants WHERE status='selected' GROUP BY trip_id)""")
    op.create_index(
        "uq_selected_plan_per_trip",
        "plan_variants",
        ["trip_id"],
        unique=True,
        postgresql_where=sa.text("status = 'selected'"),
        sqlite_where=sa.text("status = 'selected'"),
    )


def downgrade() -> None:
    op.drop_index("uq_selected_plan_per_trip", table_name="plan_variants")
