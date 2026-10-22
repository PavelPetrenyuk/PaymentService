from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from faststream import AckPolicy, FastStream
from faststream.rabbit import RabbitBroker
from faststream.rabbit.annotations import RabbitMessage
from pydantic import ValidationError

from app.config import get_settings
from app.db import Database
from app.integrations.payment_gateway import DeterministicPaymentGateway
from app.integrations.webhooks import WebhookClient
from app.logging_config import configure_logging
from app.messaging.contracts import ProcessingMessage
from app.messaging.topology import build_topology, declare_topology
from app.security import WebhookUrlPolicy
from app.services.processor import MessageDisposition, PaymentProcessor

logger = logging.getLogger(__name__)
settings = get_settings()
topology = build_topology(settings)
broker = RabbitBroker(settings.rabbitmq_url)
app = FastStream(broker)


@dataclass(slots=True)
class ConsumerRuntime:
    database: Database
    webhook_client: WebhookClient
    processor: PaymentProcessor


runtime: ConsumerRuntime | None = None


@app.on_startup
async def setup_runtime() -> None:
    global runtime
    configure_logging(settings.log_level)
    await declare_topology(settings, topology)
    database = Database(settings.database_url)
    url_policy = WebhookUrlPolicy(allow_http=settings.allow_insecure_webhooks)
    webhook_client = WebhookClient(
        signing_secret=settings.webhook_signing_secret.get_secret_value(),
        url_policy=url_policy,
        connect_timeout_seconds=settings.webhook_connect_timeout_seconds,
        read_timeout_seconds=settings.webhook_read_timeout_seconds,
    )
    gateway = DeterministicPaymentGateway(
        min_delay_seconds=settings.payment_processing_min_delay_seconds,
        max_delay_seconds=settings.payment_processing_max_delay_seconds,
    )
    processor = PaymentProcessor(
        session_factory=database.session_factory,
        gateway=gateway,
        webhook_client=webhook_client,
    )
    runtime = ConsumerRuntime(database, webhook_client, processor)


@app.after_shutdown
async def close_runtime() -> None:
    global runtime
    if runtime is None:
        return
    await runtime.webhook_client.close()
    await runtime.database.dispose()
    runtime = None


@broker.subscriber(
    topology.main_queue,
    exchange=topology.payments_exchange,
    ack_policy=AckPolicy.MANUAL,
)
async def consume_payment(body: Any, message: RabbitMessage) -> None:
    if runtime is None:
        await message.nack(requeue=True)
        return

    try:
        event = ProcessingMessage.model_validate(body)
    except ValidationError:
        logger.warning("invalid_payment_message")
        await message.reject(requeue=False)
        return

    try:
        disposition = await runtime.processor.handle(event)
    except Exception as exc:
        # A database outage may also prevent us from recording a durable retry.
        # In that case RabbitMQ keeps the original message by requeueing it.
        logger.error(
            "payment_message_unhandled_error",
            extra={
                "payment_id": str(event.payment_id),
                "attempt": event.attempt,
                "error_type": type(exc).__name__,
            },
        )
        await asyncio.sleep(0.5)
        await message.nack(requeue=True)
        return

    if disposition is MessageDisposition.REJECT:
        await message.reject(requeue=False)
    else:
        await message.ack()
