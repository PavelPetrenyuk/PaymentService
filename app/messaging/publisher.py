from __future__ import annotations

import asyncio
import logging
import signal

from faststream.rabbit import RabbitBroker

from app.config import get_settings
from app.db import Database
from app.logging_config import configure_logging
from app.messaging.contracts import PAYMENT_CREATED_EVENT
from app.messaging.topology import MAIN_ROUTING_KEY, build_topology
from app.services.outbox import (
    ClaimedOutboxEvent,
    claim_outbox_batch,
    mark_outbox_published,
    release_outbox_event,
)

logger = logging.getLogger(__name__)


async def publish_event(
    broker: RabbitBroker,
    event: ClaimedOutboxEvent,
    *,
    publish_timeout: float,
) -> None:
    settings = get_settings()
    topology = build_topology(settings)
    if event.event_type == PAYMENT_CREATED_EVENT:
        exchange = topology.payments_exchange
        routing_key = MAIN_ROUTING_KEY
    else:
        exchange = topology.retry_exchange
        routing_key = f"payments.retry.{event.attempt}"

    await broker.publish(
        event.payload,
        exchange=exchange,
        routing_key=routing_key,
        mandatory=True,
        persist=True,
        timeout=publish_timeout,
        correlation_id=str(event.id),
    )


async def run_publisher(stop: asyncio.Event) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    broker = RabbitBroker(settings.rabbitmq_url)
    try:
        await broker.connect()
        while not stop.is_set():
            events = await claim_outbox_batch(
                database.session_factory,
                batch_size=settings.outbox_batch_size,
                lease_seconds=settings.outbox_lease_seconds,
            )
            if not events:
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=settings.outbox_poll_interval_seconds
                    )
                except TimeoutError:
                    pass
            for event in events:
                try:
                    await publish_event(
                        broker,
                        event,
                        publish_timeout=settings.rabbit_publish_timeout_seconds,
                    )
                except Exception as exc:
                    logger.error(
                        "outbox_publish_failed",
                        extra={
                            "outbox_id": str(event.id),
                            "event_type": event.event_type,
                            "error_type": type(exc).__name__,
                        },
                    )
                    backoff = min(30.0, 2 ** min(event.publish_attempts, 5))
                    await release_outbox_event(
                        database.session_factory,
                        event,
                        retry_after_seconds=backoff,
                    )
                else:
                    await mark_outbox_published(database.session_factory, event)

    finally:
        await broker.stop()
        await database.dispose()


def main() -> None:
    configure_logging(get_settings().log_level)
    stop = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run_publisher(stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
