from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.core.auth import COOKIE_NAME, current_user, hash_password, issue_session, rate_limit_auth, verify_password
from backend.core.errors import AppError
from backend.db import get_db
from backend.models import AuthSession, User
from backend.settings import settings

router = APIRouter(prefix="/auth", tags=["authentication"])


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=100)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    display_name: str


class LoginRead(BaseModel):
    user: UserRead
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


def login_response(db: Session, user: User, response: Response) -> LoginRead:
    token, expires = issue_session(db, user)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_hours * 3600,
        path="/",
    )
    return LoginRead(user=UserRead.model_validate(user), access_token=token, expires_at=expires)


@router.post("/register", response_model=LoginRead, status_code=201, dependencies=[Depends(rate_limit_auth)])
def register(payload: Registration, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == payload.email)):
        raise AppError(409, "EMAIL_EXISTS", "An account with this email already exists.")
    user = User(
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password.get_secret_value()),
    )
    db.add(user)
    db.flush()
    return login_response(db, user, response)


@router.post("/login", response_model=LoginRead, dependencies=[Depends(rate_limit_auth)])
def login(payload: Credentials, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email))
    # Use the same work factor for nonexistent accounts to reduce timing leaks.
    dummy = "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32
    valid = verify_password(payload.password.get_secret_value(), user.password_hash if user else dummy)
    if not user or not valid:
        raise AppError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    return login_response(db, user, response)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(current_user)):
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.execute(delete(AuthSession).where(AuthSession.token_hash == request.state.session_hash))
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
