from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from app.models import Currency, PaymentStatus

IdempotencyKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Money
    currency: Currency
    description: Annotated[str, StringConstraints(max_length=500)]
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: AnyHttpUrl


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(serialization_alias="payment_id")
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None


class PaymentAcceptedResponse(BaseModel):
    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class ErrorResponse(BaseModel):
    detail: str
