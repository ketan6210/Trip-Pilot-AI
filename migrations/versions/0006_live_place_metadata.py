"""Add live place image, price, and provenance metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0006_live_places"
down_revision = "0005_plan_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("places", sa.Column("image_url", sa.String(1000), nullable=True))
    op.add_column("places", sa.Column("image_source", sa.String(500), nullable=True))
    op.add_column("places", sa.Column("image_attribution", sa.String(500), nullable=True))
    op.add_column("places", sa.Column("cost_status", sa.String(30), nullable=False, server_default="ESTIMATED"))
    op.add_column("places", sa.Column("cost_source", sa.String(500), nullable=True))
    op.add_column("places", sa.Column("cost_currency", sa.String(3), nullable=False, server_default="USD"))
    op.add_column("places", sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("places", "source_metadata")
    op.drop_column("places", "cost_currency")
    op.drop_column("places", "cost_source")
    op.drop_column("places", "cost_status")
    op.drop_column("places", "image_attribution")
    op.drop_column("places", "image_source")
    op.drop_column("places", "image_url")
