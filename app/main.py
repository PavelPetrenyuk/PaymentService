from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Response, status

from app.api.dependencies import require_api_key
from app.api.routes import router as payments_router
from app.config import Settings, get_settings
from app.db import Database
from app.logging_config import configure_logging
from app.security import WebhookUrlPolicy
from app.services.payments import PaymentService

MAX_REQUEST_BODY_BYTES = 64 * 1024


class RequestBodyLimitMiddleware:
    """Enforces a real body limit even when Content-Length is absent."""

    def __init__(self, app: Any, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        try:
            declared_length = int(content_length) if content_length is not None else 0
        except ValueError:
            declared_length = self.max_body_bytes + 1
        if declared_length > self.max_body_bytes:
            response = Response(
                "Request body too large", status_code=status.HTTP_413_CONTENT_TOO_LARGE
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise ValueError("request body too large")
            return message

        try:
            await self.app(scope, limited_receive, send)
        except ValueError as exc:
            if str(exc) != "request body too large":
                raise
            response = Response(
                "Request body too large", status_code=status.HTTP_413_CONTENT_TOO_LARGE
            )
            await response(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)
    database = Database(runtime_settings.database_url)
    url_policy = WebhookUrlPolicy(allow_http=runtime_settings.allow_insecure_webhooks)
    payment_service = PaymentService(url_policy)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await database.dispose()

    application = FastAPI(
        title="Async Payment Service",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = runtime_settings
    application.state.database = database
    application.state.payment_service = payment_service
    application.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=MAX_REQUEST_BODY_BYTES)
    application.include_router(payments_router)

    @application.get("/health/live", dependencies=[Depends(require_api_key)], tags=["health"])
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
