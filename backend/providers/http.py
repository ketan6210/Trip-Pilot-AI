"""Bounded retries for read-only provider operations; callers decide fallbacks."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from backend.settings import settings

T = TypeVar("T")


class ProviderError(httpx.HTTPError):
    def __init__(self, provider: str, code: str, retryable: bool, status: int = 503):
        super().__init__(f"{provider}: {code}")
        self.provider, self.code, self.retryable, self.status = provider, code, retryable, status


def classify_failure(provider: str, exc: httpx.HTTPError) -> ProviderError:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return ProviderError(provider, "PROVIDER_QUOTA_EXCEEDED", True, 429)
        if status in {400, 404, 422}:
            return ProviderError(provider, "PROVIDER_INVALID_INPUT", False, 422)
        if status in {401, 403}:
            return ProviderError(provider, "PROVIDER_UNAVAILABLE", False)
        return ProviderError(provider, "PROVIDER_TEMPORARY_FAILURE", status >= 500)
    return ProviderError(provider, "PROVIDER_TEMPORARY_FAILURE", True)


def request_with_retry(operation: Callable[[], T], provider: str, *, attempts: int | None = None) -> T:
    attempts = max(1, min(settings.provider_attempts if attempts is None else attempts, 3))
    for attempt in range(attempts):
        try:
            response = operation()
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            error = classify_failure(provider, exc)
            if not error.retryable or attempt == attempts - 1:
                raise error from exc
            time.sleep(0.1 * 2**attempt)
    raise RuntimeError("unreachable")


async def request_async(operation: Callable[[], Awaitable[T]], provider: str) -> T:
    attempts = max(1, min(settings.provider_attempts, 3))
    for attempt in range(attempts):
        try:
            response = await operation()
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            error = classify_failure(provider, exc)
            if not error.retryable or attempt == attempts - 1:
                raise error from exc
            await asyncio.sleep(0.1 * 2**attempt)
    raise RuntimeError("unreachable")
