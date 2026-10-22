from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ProcessingStage(StrEnum):
    PAYMENT = "payment"
    WEBHOOK = "webhook"


class ProcessingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    payment_id: uuid.UUID
    stage: ProcessingStage
    attempt: int = Field(ge=1, le=3)


PAYMENT_CREATED_EVENT = "payment.created.v1"
PAYMENT_RETRY_EVENT = "payment.processing.retry.v1"
WEBHOOK_RETRY_EVENT = "webhook.delivery.retry.v1"
