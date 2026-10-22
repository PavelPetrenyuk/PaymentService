from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.contracts import PAYMENT_CREATED_EVENT, ProcessingMessage, ProcessingStage
from app.models import Payment
from app.schemas import PaymentCreate
from app.security import WebhookUrlPolicy
from app.services.outbox import add_outbox_event


class IdempotencyConflictError(ValueError):
    pass


def canonical_request_hash(request: PaymentCreate) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class PaymentService:
    """Coordinates API validation with the payment/outbox database transaction."""

    def __init__(self, webhook_url_policy: WebhookUrlPolicy) -> None:
        self._webhook_url_policy = webhook_url_policy

    async def create_payment(
        self,
        session: AsyncSession,
        *,
        request: PaymentCreate,
        idempotency_key: str,
    ) -> Payment:
        webhook_url = str(request.webhook_url)
        await self._webhook_url_policy.validate(webhook_url)
        request_hash = canonical_request_hash(request)

        try:
            async with session.begin():
                existing = await self._find_by_idempotency_key(session, idempotency_key)
                if existing is not None:
                    return self._validate_existing(existing, request_hash)

                payment = Payment(
                    amount=request.amount,
                    currency=request.currency,
                    description=request.description,
                    metadata_json=request.metadata,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    webhook_url=webhook_url,
                )
                session.add(payment)
                await session.flush()

                message = ProcessingMessage(
                    event_id=payment.id,
                    event_type=PAYMENT_CREATED_EVENT,
                    occurred_at=datetime.now(UTC),
                    payment_id=payment.id,
                    stage=ProcessingStage.PAYMENT,
                    attempt=1,
                )
                add_outbox_event(
                    session,
                    event_type=PAYMENT_CREATED_EVENT,
                    aggregate_id=payment.id,
                    payload=message.model_dump(mode="json"),
                    dedupe_key=f"payment-created:{payment.id}",
                )
                return payment
        except IntegrityError:
            await session.rollback()
            existing = await self._find_by_idempotency_key(session, idempotency_key)
            if existing is None:
                raise
            return self._validate_existing(existing, request_hash)

    async def get_payment(self, session: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
        return await session.get(Payment, payment_id)

    async def _find_by_idempotency_key(
        self, session: AsyncSession, idempotency_key: str
    ) -> Payment | None:
        result = await session.execute(
            select(Payment).where(Payment.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _validate_existing(payment: Payment, request_hash: str) -> Payment:
        if payment.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency-Key was already used with a different request"
            )
        return payment
