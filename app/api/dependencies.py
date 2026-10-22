from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.services.payments import PaymentService

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_runtime_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_payment_service(request: Request) -> PaymentService:
    return request.app.state.payment_service


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.session_factory() as session:
        yield session


SuppliedApiKey = Annotated[str | None, Depends(api_key_header)]
RuntimeSettings = Annotated[Settings, Depends(get_runtime_settings)]


async def require_api_key(
    supplied_key: SuppliedApiKey,
    settings: RuntimeSettings,
) -> None:
    expected = settings.api_key.get_secret_value()
    if supplied_key is None or not hmac.compare_digest(supplied_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
