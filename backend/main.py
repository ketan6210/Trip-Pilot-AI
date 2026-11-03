from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from backend.api.ai import router as ai_router
from backend.api.auth import router as auth_router
from backend.api.legacy import router as legacy_router
from backend.api.v1 import router as v1_router
from backend.core.errors import AppError, install_error_handlers
from backend.db import SessionLocal, engine
from backend.providers.seed import seed_mock_places
from backend.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not inspect(engine).has_table("auth_sessions"):
        raise RuntimeError("Database schema is not ready. Run: alembic upgrade head")
    if settings.place_provider.strip().lower() == "mock":
        with SessionLocal() as db:
            seed_mock_places(db)
    yield


app = FastAPI(title="TripPilot AI", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)
install_error_handlers(app)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(v1_router, prefix="/api/v1")
app.include_router(ai_router, prefix="/api/v1")
app.include_router(legacy_router, include_in_schema=False)
# Keep existing planning/detail APIs available under the versioned prefix too.
compatibility = APIRouter()
defined = {(route.path, method) for route in v1_router.routes for method in route.methods}
compatibility.routes.extend(
    route for route in legacy_router.routes if not any((route.path, method) in defined for method in route.methods)
)
app.include_router(compatibility, prefix="/api/v1", tags=["planning compatibility"])


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError(503, "DATABASE_UNAVAILABLE", "Database is unavailable.") from exc
    return {"status": "ready"}
