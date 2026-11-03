import os
import subprocess
import sys

import sqlalchemy as sa


def test_existing_database_upgrade_is_reversible_and_preserves_trip(tmp_path):
    url = "sqlite:///" + str(tmp_path / "migration.db")
    environment = {**os.environ, "DATABASE_URL": url}

    def migrate(target, direction="upgrade"):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", direction, target], env=environment, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    migrate("0009_google_place_contact")
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("""INSERT INTO trips
            (id, destination_city, origin, start_date, end_date, traveler_count, total_budget,
             currency, pace, transport_mode, max_walking_per_day, hotel_style, created_at, flight_included, trip_version)
            VALUES (1, 'New York City', 'Chicago', '2027-06-01', '2027-06-03', 2, 5000,
                    'INR', 'balanced', 'mixed', 120, 'midrange', '2026-10-23 12:00:00', 1, 1)""")
        )
    migrate("head")
    with engine.connect() as connection:
        row = connection.execute(sa.text("SELECT * FROM trips WHERE id=1")).mappings().one()
        assert row["currency"] == "INR"
        assert row["total_budget"] == 5000
        assert len(row["public_id"]) == 36
        assert row["owner_id"] is None  # Never assign private legacy trips to the first account.
    assert "itinerary_activities" in sa.inspect(engine).get_table_names()
    migrate("0009_google_place_contact", "downgrade")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM trips")) == 1
    migrate("head")
