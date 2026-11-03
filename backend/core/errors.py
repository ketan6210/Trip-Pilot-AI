import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message = status, code, message
        self.details = details or {}


def error_response(status: int, code: str, message: str, details=None, headers=None):
    # `detail` keeps older UI clients compatible with the unified envelope.
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "details": details or {}},
            "detail": message,
        },
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    from backend.providers.http import ProviderError

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError):
        logger.warning(
            "Provider request failed: %s %s provider=%s code=%s retryable=%s",
            request.method,
            request.url.path,
            exc.provider,
            exc.code,
            exc.retryable,
        )
        return error_response(
            exc.status,
            exc.code,
            "Place search is temporarily unavailable. Your trip details are saved; try generating again shortly."
            if exc.provider.startswith("OpenStreetMap")
            else "An external travel provider could not complete the request. Please try again shortly.",
            {"provider": exc.provider, "retryable": exc.retryable},
        )

    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {
            400: "BAD_REQUEST",
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
            503: "PROVIDER_UNAVAILABLE",
        }
        return error_response(
            exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail), headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo submitted secrets or arbitrary request bodies in errors.
        issues = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Please check the supplied fields.", {"issues": issues})

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        return error_response(409, "CONFLICT", "This change conflicts with an existing record or constraint.")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.exception("Unhandled request failure: %s %s", request.method, request.url.path)
        return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred. Please retry.")
