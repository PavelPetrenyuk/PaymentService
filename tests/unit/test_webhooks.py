import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.integrations.webhooks import WebhookClient, WebhookDeliveryError, WebhookPayload
from app.security import WebhookUrlPolicy


async def public_resolver(_: str, __: int) -> list[str]:
    return ["93.184.216.34"]


def payload() -> WebhookPayload:
    return WebhookPayload(
        event_id=str(uuid.uuid4()),
        event_type="payment.succeeded",
        occurred_at=datetime(2026, 10, 15, tzinfo=UTC),
        attempt=1,
        payment_id=str(uuid.uuid4()),
        amount=Decimal("100.50"),
        currency="RUB",
        status="succeeded",
    )


def webhook_client(handler: httpx.AsyncBaseTransport) -> WebhookClient:
    return WebhookClient(
        signing_secret="secret",
        url_policy=WebhookUrlPolicy(allow_http=False, resolver=public_resolver),
        connect_timeout_seconds=2,
        read_timeout_seconds=5,
        transport=handler,
    )


async def test_webhook_client_sends_signed_json() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["payment"]["amount"] == "100.50"
        assert request.headers["X-Webhook-Signature"].startswith("sha256=")
        return httpx.Response(204)

    client = webhook_client(httpx.MockTransport(handler))
    try:
        await client.send("https://merchant.example/webhook", payload())
    finally:
        await client.close()


@pytest.mark.parametrize("status_code", [400, 429, 503])
async def test_webhook_client_reports_every_non_success_status(status_code: int) -> None:
    client = webhook_client(httpx.MockTransport(lambda _: httpx.Response(status_code)))
    try:
        with pytest.raises(WebhookDeliveryError) as captured:
            await client.send("https://merchant.example/webhook", payload())
        assert captured.value.category == f"http_{status_code}"
    finally:
        await client.close()


async def test_webhook_client_rejects_private_destination() -> None:
    async def private_resolver(_: str, __: int) -> list[str]:
        return ["127.0.0.1"]

    client = WebhookClient(
        signing_secret="secret",
        url_policy=WebhookUrlPolicy(allow_http=False, resolver=private_resolver),
        connect_timeout_seconds=2,
        read_timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(204)),
    )
    try:
        with pytest.raises(WebhookDeliveryError) as captured:
            await client.send("https://merchant.example/webhook", payload())
        assert captured.value.category == "unsafe_url"
    finally:
        await client.close()
