from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from app.security import UnsafeWebhookUrlError, WebhookUrlPolicy, sign_webhook


class WebhookDeliveryError(RuntimeError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True, slots=True)
class WebhookPayload:
    event_id: str
    event_type: str
    occurred_at: datetime
    attempt: int
    payment_id: str
    amount: Decimal
    currency: str
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "attempt": self.attempt,
            "payment": {
                "id": self.payment_id,
                "amount": format(self.amount, ".2f"),
                "currency": self.currency,
                "status": self.status,
            },
        }


def encode_webhook_payload(payload: WebhookPayload) -> bytes:
    return json.dumps(
        payload.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class WebhookClient:
    """Sends signed webhooks with explicit network timeouts."""

    def __init__(
        self,
        *,
        signing_secret: str,
        url_policy: WebhookUrlPolicy,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._signing_secret = signing_secret
        self._url_policy = url_policy
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout_seconds,
                read=read_timeout_seconds,
                write=read_timeout_seconds,
                pool=connect_timeout_seconds,
            ),
            follow_redirects=False,
            transport=transport,
        )

    async def send(self, url: str, payload: WebhookPayload) -> None:
        try:
            await self._url_policy.validate(url)
        except UnsafeWebhookUrlError as exc:
            raise WebhookDeliveryError("unsafe_url") from exc
        body = encode_webhook_payload(payload)
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": str(timestamp),
            "X-Webhook-Signature": sign_webhook(self._signing_secret, timestamp, body),
        }

        try:
            response = await self._client.post(url, content=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise WebhookDeliveryError("timeout") from exc
        except httpx.TransportError as exc:
            raise WebhookDeliveryError("network") from exc

        if 200 <= response.status_code < 300:
            return
        raise WebhookDeliveryError(f"http_{response.status_code}")

    async def close(self) -> None:
        await self._client.aclose()
