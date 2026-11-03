import hashlib
import hmac
import secrets
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic

from fastapi import Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.core.errors import AppError
from backend.db import get_db
from backend.models import AuthSession, Group, GroupMember, PlanVariant, Traveler, Trip, TripStateRecord, User
from backend.settings import settings

COOKIE_NAME = "trippilot_session"
PASSWORD_ITERATIONS = 600_000
_attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()
_limit_lock = Lock()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(db: Session, user: User) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.session_hours)
    db.execute(delete(AuthSession).where(AuthSession.expires_at < datetime.now(UTC)))
    db.add(AuthSession(token_hash=token_digest(token), user_id=user.id, expires_at=expires))
    db.commit()
    return token, expires


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowed = {*settings.cors_origin_list, str(request.base_url).rstrip("/")}
    if origin and origin not in allowed:
        raise AppError(403, "UNTRUSTED_ORIGIN", "This request origin is not allowed.")


def rate_limit_auth(request: Request) -> None:
    check_origin(request)
    key = request.client.host if request.client else "unknown"
    now = monotonic()
    with _limit_lock:
        started, count = _attempts.get(key, (now, 0))
        if now - started >= 60:
            started, count = now, 0
        _attempts[key] = (started, count + 1)
        _attempts.move_to_end(key)
        while len(_attempts) > 10_000:
            _attempts.popitem(last=False)
    if count >= settings.auth_rate_limit:
        raise AppError(429, "RATE_LIMITED", "Too many authentication attempts. Retry in a minute.")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    authorization = request.headers.get("authorization", "")
    if authorization and not authorization.lower().startswith("bearer "):
        raise AppError(401, "UNAUTHENTICATED", "Use Bearer authentication or a session cookie.")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else request.cookies.get(COOKIE_NAME)
    if not token:
        raise AppError(401, "UNAUTHENTICATED", "Sign in to continue.")
    session = db.get(AuthSession, token_digest(token))
    if not session:
        raise AppError(401, "SESSION_EXPIRED", "Your session has expired. Sign in again.")
    expires = session.expires_at.replace(tzinfo=UTC) if session.expires_at.tzinfo is None else session.expires_at
    if expires <= datetime.now(UTC):
        raise AppError(401, "SESSION_EXPIRED", "Your session has expired. Sign in again.")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not authorization:
        check_origin(request)
        if request.headers.get("x-requested-with") != "TripPilot":
            raise AppError(
                403, "CSRF_CHECK_FAILED", "Cookie-authenticated changes require X-Requested-With: TripPilot."
            )
    user = db.get(User, session.user_id)
    if not user:
        raise AppError(401, "UNAUTHENTICATED", "Sign in to continue.")
    request.state.user = user
    request.state.session_hash = session.token_hash
    return user


def require_trip(db: Session, trip_id: int, user: User, *, edit: bool = False, owner: bool = False) -> Trip:
    trip = db.get(Trip, trip_id)
    if not trip:
        raise AppError(404, "TRIP_NOT_FOUND", "Trip not found.")
    if trip.owner_id == user.id:
        return trip
    member = db.scalar(
        select(GroupMember)
        .join(Group, Group.id == GroupMember.group_id)
        .where(
            Group.trip_id == trip_id,
            GroupMember.user_id == user.id,
        )
    )
    if not member or owner or (edit and member.role != "EDITOR"):
        raise AppError(403, "FORBIDDEN", "You do not have permission to access or change this trip.")
    return trip


def require_plan(db: Session, plan_id: int, user: User, *, edit: bool = False, owner: bool = False) -> PlanVariant:
    plan = db.get(PlanVariant, plan_id)
    if not plan:
        raise AppError(404, "ITINERARY_NOT_FOUND", "Itinerary not found.")
    require_trip(db, plan.trip_id, user, edit=edit, owner=owner)
    return plan


def protect_legacy_resources(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> None:
    """Apply the same access policy to every compatibility route, including detail/expenses."""
    edit = request.method not in {"GET", "HEAD", "OPTIONS"}
    params = request.path_params
    # Dependencies run before path coercion; malformed identifiers remain 422s.
    try:
        if "trip_id" in params:
            require_trip(db, int(params["trip_id"]), user, edit=edit)
        if "plan_id" in params:
            plan = require_plan(
                db, int(params["plan_id"]), user, edit=edit, owner=request.url.path.endswith(("/select", "/vote"))
            )
            if edit and plan.summary.get("engine") == "cp_sat" and not request.url.path.endswith(("/select", "/vote")):
                raise AppError(
                    409,
                    "VERSIONED_EDIT_REQUIRED",
                    "Use the versioned itinerary replan endpoint to preserve optimized constraints and history.",
                )
        if "traveler_id" in params:
            traveler = db.get(Traveler, int(params["traveler_id"]))
            if not traveler:
                raise AppError(404, "TRAVELER_NOT_FOUND", "Traveler not found.")
            require_trip(db, traveler.trip_id, user, edit=edit)
        if "session_id" in params:
            record = db.get(TripStateRecord, int(params["session_id"]))
            if not record:
                raise AppError(404, "SESSION_NOT_FOUND", "Conversation not found.")
            if record.owner_id != user.id:
                raise AppError(403, "FORBIDDEN", "This conversation belongs to another user.")
            if record.trip_id:
                require_trip(db, record.trip_id, user, edit=edit)
    except (ValueError, OverflowError) as exc:
        raise AppError(422, "INVALID_IDENTIFIER", "Identifiers must be integers.") from exc
