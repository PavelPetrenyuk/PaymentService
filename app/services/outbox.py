from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import OutboxEvent


@dataclass(frozen=True, slots=True)
class ClaimedOutboxEvent:
    id: uuid.UUID
    lock_token: uuid.UUID
    event_type: str
    aggregate_id: uuid.UUID
    payload: dict[str, Any]
    attempt: int
    publish_attempts: int


def utc_now() -> datetime:
    return datetime.now(UTC)


def add_outbox_event(
    session: AsyncSession,
    *,
    event_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, Any],
    attempt: int = 1,
    dedupe_key: str | None = None,
    available_at: datetime | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        event_type=event_type,
        aggregate_id=aggregate_id,
        payload=payload,
        attempt=attempt,
        dedupe_key=dedupe_key,
        available_at=available_at or utc_now(),
    )
    session.add(event)
    return event


async def claim_outbox_batch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int,
    lease_seconds: int,
) -> list[ClaimedOutboxEvent]:
    now = utc_now()
    lease_until = now + timedelta(seconds=lease_seconds)

    async with session_factory() as session, session.begin():
        result = await session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.available_at <= now,
                or_(OutboxEvent.locked_until.is_(None), OutboxEvent.locked_until < now),
            )
            .order_by(OutboxEvent.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars())
        claimed: list[ClaimedOutboxEvent] = []
        for event in events:
            token = uuid.uuid4()
            event.lock_token = token
            event.locked_until = lease_until
            claimed.append(
                ClaimedOutboxEvent(
                    id=event.id,
                    lock_token=token,
                    event_type=event.event_type,
                    aggregate_id=event.aggregate_id,
                    payload=event.payload,
                    attempt=event.attempt,
                    publish_attempts=event.publish_attempts,
                )
            )
        return claimed


async def mark_outbox_published(
    session_factory: async_sessionmaker[AsyncSession], event: ClaimedOutboxEvent
) -> bool:
    async with session_factory() as session, session.begin():
        result = await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event.id,
                OutboxEvent.lock_token == event.lock_token,
                OutboxEvent.published_at.is_(None),
            )
            .values(
                published_at=utc_now(),
                locked_until=None,
                lock_token=None,
                publish_attempts=OutboxEvent.publish_attempts + 1,
            )
        )
        return getattr(result, "rowcount", 0) == 1


async def release_outbox_event(
    session_factory: async_sessionmaker[AsyncSession],
    event: ClaimedOutboxEvent,
    *,
    retry_after_seconds: float,
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event.id,
                OutboxEvent.lock_token == event.lock_token,
                OutboxEvent.published_at.is_(None),
            )
            .values(
                locked_until=None,
                lock_token=None,
                available_at=utc_now() + timedelta(seconds=retry_after_seconds),
                publish_attempts=OutboxEvent.publish_attempts + 1,
            )
        )
