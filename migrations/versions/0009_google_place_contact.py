"""Add provider-grounded place ratings and contact details."""

import sqlalchemy as sa
from alembic import op

revision = "0009_google_place_contact"
down_revision = "0008_context_meals_expenses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("places") as batch:
        batch.add_column(sa.Column("rating_source", sa.String(100), nullable=True))
        batch.add_column(sa.Column("google_place_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("phone_number", sa.String(80), nullable=True))
        batch.add_column(sa.Column("phone_source_url", sa.String(1000), nullable=True))
        batch.create_index("ix_places_google_place_id", ["google_place_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("places") as batch:
        batch.drop_index("ix_places_google_place_id")
        batch.drop_column("phone_source_url")
        batch.drop_column("phone_number")
        batch.drop_column("google_place_id")
        batch.drop_column("rating_source")
