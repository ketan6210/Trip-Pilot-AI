from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class PriceType(StrEnum):
    LIVE = "LIVE"
    ESTIMATED = "ESTIMATED"
    FREE = "FREE"
    UNKNOWN = "UNKNOWN"
    USER_ALLOWANCE = "USER_ALLOWANCE"


class CostScope(StrEnum):
    SHARED = "SHARED"
    PER_PERSON = "PER_PERSON"


class PriceEstimate(BaseModel):
    minimum: float | None = Field(default=None, ge=0)
    expected: float | None = Field(default=None, ge=0)
    maximum: float | None = Field(default=None, ge=0)
    currency: str
    price_type: PriceType
    provider: str | None = None
    source: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider_amount: float | None = None
    provider_currency: str | None = None
    exchange_rate: float | None = None
    rate_timestamp: datetime | None = None

    @model_validator(mode="after")
    def zero_only_means_free(self):
        values = (self.minimum, self.expected, self.maximum)
        if self.price_type == PriceType.UNKNOWN and any(value == 0 for value in values if value is not None):
            raise ValueError("UNKNOWN price cannot contain zero")
        if self.price_type == PriceType.FREE:
            self.minimum = self.expected = self.maximum = 0.0
        return self


class CostLedgerEntry(BaseModel):
    cost_id: str
    plan_id: int | None = None
    category: str
    item_id: str | None = None
    minimum: float | None = None
    expected: float | None = None
    maximum: float | None = None
    currency: str
    shared_or_personal: CostScope
    price_type: PriceType
    confidence: float = 0
    provider: str | None = None
    source: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict)
