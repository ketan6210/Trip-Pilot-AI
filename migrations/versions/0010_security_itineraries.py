"""Account ownership, groups, sessions and normalized itinerary activities."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0010_security_itineraries"
down_revision = "0009_google_place_contact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    with op.batch_alter_table("trips") as batch:
        batch.add_column(sa.Column("public_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("owner_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("status", sa.String(20), nullable=False, server_default="draft"))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_trips_owner", "users", ["owner_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_trips_owner_id", ["owner_id"])
        batch.create_unique_constraint("uq_trips_public_id", ["public_id"])
        batch.create_check_constraint("ck_trip_dates", "end_date >= start_date")
        batch.create_check_constraint("ck_trip_travelers", "traveler_count BETWEEN 1 AND 20")
        batch.create_check_constraint("ck_trip_budget", "total_budget > 0")
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT id FROM trips")).all():
        bind.execute(
            sa.text("UPDATE trips SET public_id=:uuid, updated_at=created_at WHERE id=:id"),
            {"uuid": str(uuid4()), "id": row.id},
        )
    with op.batch_alter_table("trips") as batch:
        batch.alter_column("public_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    with op.batch_alter_table("trip_states") as batch:
        batch.add_column(sa.Column("owner_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_trip_states_owner", "users", ["owner_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_trip_states_owner_id", ["owner_id"])
    op.create_table(
        "groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, unique=True),
    )
    op.create_table(
        "group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.UniqueConstraint("group_id", "user_id"),
        sa.CheckConstraint("role IN ('OWNER', 'EDITOR', 'VIEWER')", name="ck_member_role"),
    )
    op.create_table(
        "group_votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan_variants.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("group_id", "user_id"),
    )
    for table in ("group_members", "group_votes"):
        for column in ("group_id", "user_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index("ix_group_votes_plan_id", "group_votes", ["plan_id"])
    op.create_table(
        "itinerary_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plan_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.UniqueConstraint("plan_id", "day_number"),
    )
    op.create_index("ix_itinerary_days_plan_id", "itinerary_days", ["plan_id"])
    op.create_table(
        "itinerary_activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day_id", sa.Integer(), sa.ForeignKey("itinerary_days.id", ondelete="CASCADE"), nullable=False),
        sa.Column("place_id", sa.Integer(), sa.ForeignKey("places.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("reason_for_selection", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.UniqueConstraint("day_id", "position"),
        sa.CheckConstraint("end_minute > start_minute AND start_minute >= 0", name="ck_activity_time"),
        sa.CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0", name="ck_activity_cost"),
    )
    for column in ("day_id", "place_id"):
        op.create_index(f"ix_itinerary_activities_{column}", "itinerary_activities", [column])
    # Existing plans/details remain intact. No user is assigned ownership implicitly.
    _backfill_details(bind)


def _backfill_details(bind) -> None:
    from datetime import date

    metadata = sa.MetaData()
    details = sa.Table("plan_details", metadata, autoload_with=bind, resolve_fks=False)
    days = sa.Table("itinerary_days", metadata, autoload_with=bind, resolve_fks=False)
    activities = sa.Table("itinerary_activities", metadata, autoload_with=bind, resolve_fks=False)
    places = {row.id: row for row in bind.execute(sa.text("SELECT id, latitude, longitude FROM places"))}
    for record in bind.execute(sa.select(details.c.plan_id, details.c.detail)):
        payload = record.detail or {}
        currency = payload.get("trip", {}).get("currency")
        if not currency:
            continue
        for index, day in enumerate(payload.get("days", []), 1):
            day_id = bind.execute(
                days.insert().values(plan_id=record.plan_id, day_number=index, date=date.fromisoformat(day["date"]))
            ).inserted_primary_key[0]
            for position, item in enumerate(day.get("timeline") or day.get("items", [])):
                place = places.get(item.get("place_id") or item.get("restaurant_id"))
                if not place or item.get("end_minute", 0) <= item.get("start_minute", 0):
                    continue
                bind.execute(
                    activities.insert().values(
                        day_id=day_id,
                        place_id=place.id,
                        position=position,
                        start_minute=item["start_minute"],
                        end_minute=item["end_minute"],
                        estimated_cost=item.get("group_cost_expected", item.get("expected_total")),
                        currency=currency,
                        category=item.get("category", "food"),
                        reason_for_selection=item.get("why_selected", "Migrated from stored itinerary"),
                        latitude=place.latitude,
                        longitude=place.longitude,
                    )
                )


def downgrade() -> None:
    for table in ("itinerary_activities", "itinerary_days", "group_votes", "group_members", "groups"):
        op.drop_table(table)
    with op.batch_alter_table("trip_states") as batch:
        batch.drop_index("ix_trip_states_owner_id")
        batch.drop_constraint("fk_trip_states_owner", type_="foreignkey")
        batch.drop_column("owner_id")
    with op.batch_alter_table("trips") as batch:
        batch.drop_index("ix_trips_owner_id")
        batch.drop_constraint("fk_trips_owner", type_="foreignkey")
        batch.drop_constraint("uq_trips_public_id", type_="unique")
        for constraint in ("ck_trip_dates", "ck_trip_travelers", "ck_trip_budget"):
            batch.drop_constraint(constraint, type_="check")
        for column in ("owner_id", "public_id", "status", "updated_at"):
            batch.drop_column(column)
    op.drop_table("auth_sessions")
    op.drop_table("users")
