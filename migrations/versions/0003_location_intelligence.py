"""Add normalized places and PostGIS geography search support."""

import sqlalchemy as sa
from alembic import op

revision = "0003_location"
down_revision = "0002_phase_two"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "places",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_provider", sa.String(40), nullable=False),
        sa.Column("external_place_id", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("state", sa.String(100), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("borough_or_district", sa.String(100), nullable=False),
        sa.Column("neighborhood", sa.String(100), nullable=False),
        sa.Column("address", sa.String(300), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("subcategory", sa.String(50), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("review_count", sa.Integer(), nullable=False),
        sa.Column("price_level", sa.Integer(), nullable=False),
        sa.Column("cost_min", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_expected", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_max", sa.Numeric(10, 2), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("opening_hours", sa.JSON(), nullable=False),
        sa.Column("indoor_outdoor", sa.String(20), nullable=False),
        sa.Column("age_restriction", sa.Integer(), nullable=True),
        sa.Column("reservation_required", sa.Boolean(), nullable=False),
        sa.Column("official_url", sa.String(500), nullable=True),
        sa.Column("maps_url", sa.String(500), nullable=False),
        sa.Column("ticket_url", sa.String(500), nullable=True),
        sa.Column("reservation_url", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("external_provider", "external_place_id"),
    )
    op.create_index("ix_places_city_category", "places", ["city", "category"])
    op.create_index("ix_places_city_neighborhood", "places", ["city", "neighborhood"])
    op.create_index("ix_places_coordinates", "places", ["latitude", "longitude"])
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE places ADD COLUMN location geography(POINT, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography) STORED"
        )
        op.execute("CREATE INDEX ix_places_location_gist ON places USING GIST (location)")


def downgrade() -> None:
    op.drop_table("places")
