"""Explicit administrator-only ownership assignment for pre-authentication trips.

Run as a module after registering the intended owner. No HTTP claim endpoint exists.
"""

import argparse

from sqlalchemy import select

from backend.db import SessionLocal
from backend.domain.trips import attach_owner
from backend.models import Trip, TripStateRecord, User


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--trip-id", action="append", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="Apply the displayed ownership assignment")
    args = parser.parse_args()
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.email == args.email.strip().casefold()))
        if not owner:
            parser.error("Register the intended owner's account before assigning trips")
        trips = list(db.scalars(select(Trip).where(Trip.id.in_(args.trip_id)).with_for_update()))
        if len(trips) != len(set(args.trip_id)) or any(trip.owner_id for trip in trips):
            parser.error("Every requested trip must exist and have no owner")
        print(f"Assign trips {sorted(trip.id for trip in trips)} to {owner.email}")
        if not args.apply:
            print("Dry run. Add --apply to perform this assignment.")
            return
        for trip in trips:
            attach_owner(db, trip, owner.id)
            for conversation in db.scalars(select(TripStateRecord).where(TripStateRecord.trip_id == trip.id)):
                conversation.owner_id = owner.id
        db.commit()
        print("Ownership assigned.")


if __name__ == "__main__":
    main()
