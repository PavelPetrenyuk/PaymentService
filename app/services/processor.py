from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.payment_gateway import DeterministicPaymentGateway
from app.integrations.webhooks import WebhookClient, WebhookDeliveryError, WebhookPayload
from app.messaging.contracts import (
    PAYMENT_RETRY_EVENT,
    WEBHOOK_RETRY_EVENT,
    ProcessingMessage,
    ProcessingStage,
)
from app.models import Payment, PaymentStatus
from app.services.outbox import add_outbox_event

logger = logging.getLogger(__name__)


class MessageDisposition(StrEnum):
    ACK = "ack"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class PaymentSnapshot:
    id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    webhook_url: str


class PaymentProcessor:
    MAX_ATTEMPTS = 3

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: DeterministicPaymentGateway,
        webhook_client: WebhookClient,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._webhook_client = webhook_client

    async def handle(self, message: ProcessingMessage) -> MessageDisposition:
        if message.stage is ProcessingStage.PAYMENT:
            return await self._process_payment(message)
        return await self._deliver_webhook(message)

    async def _process_payment(self, message: ProcessingMessage) -> MessageDisposition:
        payment = await self._get_payment(message.payment_id)
        if payment is None:
            logger.warning("payment_missing", extra={"payment_id": str(message.payment_id)})
            return MessageDisposition.ACK

        if payment.status is PaymentStatus.PENDING:
            if message.attempt <= payment.processing_attempts:
                return MessageDisposition.ACK
            try:
                succeeded = await self._gateway.process(payment.id)
            except Exception as exc:
                logger.error(
                    "payment_gateway_error",
                    extra={
                        "payment_id": str(payment.id),
                        "attempt": message.attempt,
                        "error_type": type(exc).__name__,
                    },
                )
                return await self._record_processing_failure(message)
            await self._finish_payment(payment.id, succeeded=succeeded, attempt=message.attempt)

        webhook_message = ProcessingMessage(
            event_id=payment.id,
            event_type="webhook.delivery.v1",
            occurred_at=datetime.now(UTC),
            payment_id=payment.id,
            stage=ProcessingStage.WEBHOOK,
            attempt=1,
        )
        return await self._deliver_webhook(webhook_message)

    async def _get_payment(self, payment_id: uuid.UUID) -> Payment | None:
        async with self._session_factory() as session:
            return await session.get(Payment, payment_id)

    async def _finish_payment(
        self, payment_id: uuid.UUID, *, succeeded: bool, attempt: int
    ) -> None:
        async with self._session_factory() as session, session.begin():
            payment = await session.get(Payment, payment_id, with_for_update=True)
            if payment is None:
                raise RuntimeError("payment disappeared during processing")
            if payment.status is not PaymentStatus.PENDING:
                return
            payment.status = PaymentStatus.SUCCEEDED if succeeded else PaymentStatus.FAILED
            payment.processing_attempts = attempt
            payment.processed_at = datetime.now(UTC)

    async def _record_processing_failure(self, message: ProcessingMessage) -> MessageDisposition:
        async with self._session_factory() as session, session.begin():
            payment = await session.get(Payment, message.payment_id, with_for_update=True)
            if payment is None or payment.status is not PaymentStatus.PENDING:
                return MessageDisposition.ACK
            if message.attempt <= payment.processing_attempts:
                return MessageDisposition.ACK

            payment.processing_attempts = message.attempt
            if message.attempt >= self.MAX_ATTEMPTS:
                payment.status = PaymentStatus.FAILED
                payment.processed_at = datetime.now(UTC)
                return MessageDisposition.REJECT

            self._schedule_retry(
                session,
                payment=payment,
                stage=ProcessingStage.PAYMENT,
                attempt=message.attempt + 1,
            )
            return MessageDisposition.ACK

    async def _deliver_webhook(self, message: ProcessingMessage) -> MessageDisposition:
        snapshot = await self._load_webhook_snapshot(message)
        if snapshot is None:
            return MessageDisposition.ACK

        payload = WebhookPayload(
            event_id=str(snapshot.id),
            event_type=f"payment.{snapshot.status}",
            occurred_at=datetime.now(UTC),
            attempt=message.attempt,
            payment_id=str(snapshot.id),
            amount=snapshot.amount,
            currency=snapshot.currency,
            status=snapshot.status,
        )
        try:
            await self._webhook_client.send(snapshot.webhook_url, payload)
        except WebhookDeliveryError as exc:
            logger.warning(
                "webhook_delivery_failed",
                extra={
                    "payment_id": str(snapshot.id),
                    "attempt": message.attempt,
                    "error_category": exc.category,
                },
            )
            return await self._record_webhook_failure(message)
        except Exception as exc:
            logger.error(
                "webhook_unexpected_error",
                extra={
                    "payment_id": str(snapshot.id),
                    "attempt": message.attempt,
                    "error_type": type(exc).__name__,
                },
            )
            return await self._record_webhook_failure(message)

        await self._finish_webhook_attempt(snapshot.id, attempt=message.attempt)
        return MessageDisposition.ACK

    async def _load_webhook_snapshot(self, message: ProcessingMessage) -> PaymentSnapshot | None:
        async with self._session_factory() as session:
            payment = await session.get(Payment, message.payment_id)
            if payment is None:
                return None
            if payment.status not in {PaymentStatus.SUCCEEDED, PaymentStatus.FAILED}:
                return None
            if message.attempt != payment.webhook_attempts + 1:
                return None
            return PaymentSnapshot(
                id=payment.id,
                amount=payment.amount,
                currency=payment.currency.value,
                status=payment.status.value,
                webhook_url=payment.webhook_url,
            )

    async def _record_webhook_failure(self, message: ProcessingMessage) -> MessageDisposition:
        async with self._session_factory() as session, session.begin():
            payment = await session.get(Payment, message.payment_id, with_for_update=True)
            if payment is None:
                return MessageDisposition.ACK
            if message.attempt <= payment.webhook_attempts:
                return MessageDisposition.ACK

            payment.webhook_attempts = message.attempt
            if message.attempt >= self.MAX_ATTEMPTS:
                return MessageDisposition.REJECT

            self._schedule_retry(
                session,
                payment=payment,
                stage=ProcessingStage.WEBHOOK,
                attempt=message.attempt + 1,
            )
            return MessageDisposition.ACK

    async def _finish_webhook_attempt(self, payment_id: uuid.UUID, *, attempt: int) -> None:
        async with self._session_factory() as session, session.begin():
            payment = await session.get(Payment, payment_id, with_for_update=True)
            if payment is None or attempt <= payment.webhook_attempts:
                return
            payment.webhook_attempts = attempt

    def _schedule_retry(
        self,
        session: AsyncSession,
        *,
        payment: Payment,
        stage: ProcessingStage,
        attempt: int,
    ) -> None:
        event_type = (
            PAYMENT_RETRY_EVENT if stage is ProcessingStage.PAYMENT else WEBHOOK_RETRY_EVENT
        )
        message = ProcessingMessage(
            event_id=payment.id,
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            payment_id=payment.id,
            stage=stage,
            attempt=attempt,
        )
        add_outbox_event(
            session,
            event_type=event_type,
            aggregate_id=payment.id,
            payload=message.model_dump(mode="json"),
            attempt=attempt,
            dedupe_key=f"{stage.value}:{payment.id}:{attempt}",
        )
