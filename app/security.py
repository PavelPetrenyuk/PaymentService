from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit


class UnsafeWebhookUrlError(ValueError):
    pass


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def resolve_host(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({record[4][0] for record in records})


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global


class WebhookUrlPolicy:
    """Rejects webhook destinations that could reach local or private services."""

    def __init__(self, *, allow_http: bool, resolver: Resolver = resolve_host) -> None:
        self._allow_http = allow_http
        self._resolver = resolver

    async def validate(self, url: str) -> None:
        parsed = urlsplit(url)
        allowed_schemes = {"https", "http"} if self._allow_http else {"https"}
        if parsed.scheme.lower() not in allowed_schemes:
            raise UnsafeWebhookUrlError("webhook URL scheme is not allowed")
        if parsed.username or parsed.password:
            raise UnsafeWebhookUrlError("webhook URL must not contain credentials")
        if not parsed.hostname:
            raise UnsafeWebhookUrlError("webhook URL must contain a host")

        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            addresses = await self._resolver(parsed.hostname, port)
        except (OSError, UnicodeError) as exc:
            raise UnsafeWebhookUrlError("webhook host cannot be resolved") from exc
        if not addresses:
            raise UnsafeWebhookUrlError("webhook host has no addresses")
        if any(not is_public_ip(address) for address in addresses):
            raise UnsafeWebhookUrlError("webhook host resolves to a non-public address")


def sign_webhook(secret: str, timestamp: int, body: bytes) -> str:
    signed_payload = str(timestamp).encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
