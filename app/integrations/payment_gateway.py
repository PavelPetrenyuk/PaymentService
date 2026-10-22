from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable

Sleeper = Callable[[float], Awaitable[None]]


class DeterministicPaymentGateway:
    """Emulates a stable 90/10 payment result with a 2-5 second delay."""

    def __init__(
        self,
        *,
        min_delay_seconds: float,
        max_delay_seconds: float,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._min_delay_seconds = min_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._sleeper = sleeper

    async def process(self, payment_id: uuid.UUID) -> bool:
        digest = hashlib.sha256(payment_id.bytes).digest()
        fraction = int.from_bytes(digest[8:16], "big") / (2**64 - 1)
        delay = (
            self._min_delay_seconds + (self._max_delay_seconds - self._min_delay_seconds) * fraction
        )
        await self._sleeper(delay)
        return int.from_bytes(digest[:8], "big") % 10 != 0
