import os
import tempfile
from pathlib import Path

test_directory = tempfile.TemporaryDirectory(prefix="trippilot-tests-")
test_url = os.environ.get("TEST_DATABASE_URL")
if test_url:
    from sqlalchemy.engine import make_url

    if not (make_url(test_url).database or "").endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must name a dedicated database ending in _test")
os.environ["DATABASE_URL"] = test_url or "sqlite:///" + str(Path(test_directory.name) / "test.db")
os.environ["ORCHESTRATOR_PROVIDER"] = "deterministic"
os.environ["PLACE_PROVIDER"] = "mock"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.core.auth import _attempts, hash_password, issue_session
from backend.db import Base, SessionLocal, engine
from backend.main import app
from backend.models import User


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    if test_url:
        command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database):
    _attempts.clear()
    if test_url:
        with engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())
    else:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    yield
    if not test_url:
        Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def password_hash():
    return hash_password("testing-password-123")


@pytest.fixture
def client(password_hash):
    with TestClient(app) as test_client:
        with SessionLocal() as db:
            user = User(email="owner@example.com", display_name="Owner", password_hash=password_hash)
            db.add(user)
            db.flush()
            token, _ = issue_session(db, user)
        test_client.headers["Authorization"] = f"Bearer {token}"
        yield test_client
