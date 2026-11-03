"""Planning policies, account preferences, ballots, immutable versions and telemetry."""

import sqlalchemy as sa
from alembic import op

revision = "0012_ai_planning"
down_revision = "0011_selected_plan"
branch_labels = None
depends_on = None


def fk(column, target, nullable=False, primary_key=False, ondelete="CASCADE", type_=sa.Integer()):
    return sa.Column(
        column, type_, sa.ForeignKey(target, ondelete=ondelete), nullable=nullable, primary_key=primary_key
    )


def upgrade():
    with op.batch_alter_table("travelers") as batch:
        batch.add_column(sa.Column("user_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("preference_text", sa.Text(), server_default="", nullable=False))
        batch.add_column(sa.Column("budget_preference", sa.Numeric(12, 2), nullable=True))
        batch.create_foreign_key("fk_traveler_user", "users", ["user_id"], ["id"], ondelete="SET NULL")
        batch.create_unique_constraint("uq_traveler_account", ["trip_id", "user_id"])
        batch.create_index("ix_travelers_user_id", ["user_id"])
    op.create_table(
        "planning_policies",
        fk("trip_id", "trips.id", primary_key=True),
        sa.Column("constraints", sa.JSON(), nullable=False),
    )
    op.create_table(
        "traveler_place_constraints",
        sa.Column("id", sa.Integer(), primary_key=True),
        fk("traveler_id", "travelers.id"),
        fk("place_id", "places.id", ondelete="RESTRICT"),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.UniqueConstraint("traveler_id", "place_id"),
        sa.CheckConstraint("kind IN ('must', 'veto')"),
    )
    op.create_table(
        "planning_ballots",
        sa.Column("id", sa.Integer(), primary_key=True),
        fk("trip_id", "trips.id"),
        fk("user_id", "users.id", type_=sa.String(36)),
        fk("plan_id", "plan_variants.id", nullable=True),
        fk("place_id", "places.id", nullable=True),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.UniqueConstraint("trip_id", "user_id", "plan_id"),
        sa.UniqueConstraint("trip_id", "user_id", "place_id"),
        sa.CheckConstraint("(plan_id IS NULL) != (place_id IS NULL)", name="ck_ballot_target"),
        sa.CheckConstraint("value BETWEEN -1 AND 1", name="ck_ballot_value"),
        sa.CheckConstraint("rank IS NULL OR rank BETWEEN 1 AND 20", name="ck_ballot_rank"),
    )
    op.create_table(
        "planning_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        fk("trip_id", "trips.id", nullable=True),
        fk("session_id", "trip_states.id", nullable=True),
        fk("user_id", "users.id", nullable=True, type_=sa.String(36), ondelete="SET NULL"),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        fk("run_id", "planning_runs.id", type_=sa.String(36)),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(14, 8), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
    )
    op.create_table(
        "itinerary_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        fk("plan_id", "plan_variants.id"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trip_version", sa.Integer(), nullable=False),
        fk("actor_id", "users.id", type_=sa.String(36), nullable=True, ondelete="SET NULL"),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "version"),
    )
    for table, columns in {
        "traveler_place_constraints": ["traveler_id", "place_id"],
        "planning_ballots": ["trip_id", "user_id", "plan_id", "place_id"],
        "planning_runs": ["trip_id", "session_id", "user_id"],
        "llm_calls": ["run_id"],
        "itinerary_versions": ["plan_id"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in (
        "itinerary_versions",
        "llm_calls",
        "planning_runs",
        "planning_ballots",
        "traveler_place_constraints",
        "planning_policies",
    ):
        op.drop_table(table)
    with op.batch_alter_table("travelers") as batch:
        batch.drop_index("ix_travelers_user_id")
        batch.drop_constraint("uq_traveler_account", type_="unique")
        batch.drop_constraint("fk_traveler_user", type_="foreignkey")
        batch.drop_column("budget_preference")
        batch.drop_column("preference_text")
        batch.drop_column("user_id")
