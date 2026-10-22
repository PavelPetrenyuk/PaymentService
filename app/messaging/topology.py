from __future__ import annotations

from dataclasses import dataclass

import aio_pika
from aio_pika import ExchangeType as AioPikaExchangeType
from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

from app.config import Settings

PAYMENTS_EXCHANGE_NAME = "payments"
RETRY_EXCHANGE_NAME = "payments.retry"
DEAD_LETTER_EXCHANGE_NAME = "payments.dlx"
MAIN_QUEUE_NAME = "payments.new"
DEAD_LETTER_QUEUE_NAME = "payments.dlq"
MAIN_ROUTING_KEY = "payments.new"
DEAD_ROUTING_KEY = "payments.dead"


@dataclass(frozen=True, slots=True)
class RabbitTopology:
    payments_exchange: RabbitExchange
    retry_exchange: RabbitExchange
    dead_letter_exchange: RabbitExchange
    main_queue: RabbitQueue
    retry_second_queue: RabbitQueue
    retry_third_queue: RabbitQueue
    dead_letter_queue: RabbitQueue


def build_topology(settings: Settings) -> RabbitTopology:
    payments_exchange = RabbitExchange(
        PAYMENTS_EXCHANGE_NAME, type=ExchangeType.DIRECT, durable=True
    )
    retry_exchange = RabbitExchange(RETRY_EXCHANGE_NAME, type=ExchangeType.DIRECT, durable=True)
    dead_letter_exchange = RabbitExchange(
        DEAD_LETTER_EXCHANGE_NAME, type=ExchangeType.DIRECT, durable=True
    )
    main_queue = RabbitQueue(
        MAIN_QUEUE_NAME,
        durable=True,
        routing_key=MAIN_ROUTING_KEY,
        arguments={
            "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE_NAME,
            "x-dead-letter-routing-key": DEAD_ROUTING_KEY,
            "x-single-active-consumer": True,
        },
    )
    retry_second_queue = _retry_queue(
        attempt=2,
        delay_seconds=settings.retry_delay_seconds(2),
    )
    retry_third_queue = _retry_queue(
        attempt=3,
        delay_seconds=settings.retry_delay_seconds(3),
    )
    dead_letter_queue = RabbitQueue(
        DEAD_LETTER_QUEUE_NAME,
        durable=True,
        routing_key=DEAD_ROUTING_KEY,
    )
    return RabbitTopology(
        payments_exchange=payments_exchange,
        retry_exchange=retry_exchange,
        dead_letter_exchange=dead_letter_exchange,
        main_queue=main_queue,
        retry_second_queue=retry_second_queue,
        retry_third_queue=retry_third_queue,
        dead_letter_queue=dead_letter_queue,
    )


def _retry_queue(*, attempt: int, delay_seconds: int) -> RabbitQueue:
    return RabbitQueue(
        f"payments.retry.{attempt}",
        durable=True,
        routing_key=f"payments.retry.{attempt}",
        arguments={
            "x-message-ttl": delay_seconds * 1000,
            "x-dead-letter-exchange": PAYMENTS_EXCHANGE_NAME,
            "x-dead-letter-routing-key": MAIN_ROUTING_KEY,
        },
    )


async def declare_topology(settings: Settings, topology: RabbitTopology) -> None:
    """Declares queues with aio-pika because retry/DLQ queues have no consumers."""

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    try:
        channel = await connection.channel(publisher_confirms=True)
        payments_exchange = await channel.declare_exchange(
            topology.payments_exchange.name,
            AioPikaExchangeType.DIRECT,
            durable=True,
        )
        retry_exchange = await channel.declare_exchange(
            topology.retry_exchange.name,
            AioPikaExchangeType.DIRECT,
            durable=True,
        )
        dead_exchange = await channel.declare_exchange(
            topology.dead_letter_exchange.name,
            AioPikaExchangeType.DIRECT,
            durable=True,
        )

        main_queue = await channel.declare_queue(
            topology.main_queue.name,
            durable=True,
            arguments=topology.main_queue.arguments,
        )
        await main_queue.bind(payments_exchange, MAIN_ROUTING_KEY)

        for queue_config in (topology.retry_second_queue, topology.retry_third_queue):
            queue = await channel.declare_queue(
                queue_config.name,
                durable=True,
                arguments=queue_config.arguments,
            )
            await queue.bind(retry_exchange, queue_config.name)

        dead_queue = await channel.declare_queue(
            topology.dead_letter_queue.name,
            durable=True,
        )
        await dead_queue.bind(dead_exchange, DEAD_ROUTING_KEY)
    finally:
        await connection.close()
