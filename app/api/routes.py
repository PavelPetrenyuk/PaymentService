from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_payment_service, get_session, require_api_key
from app.schemas import (
    ErrorResponse,
    IdempotencyKey,
    PaymentAcceptedResponse,
    PaymentCreate,
    PaymentResponse,
)
from app.security import UnsafeWebhookUrlError
from app.services.payments import IdempotencyConflictError, PaymentService

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PaymentServiceDependency = Annotated[PaymentService, Depends(get_payment_service)]

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["payments"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "",
    response_model=PaymentAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_payment(
    request: PaymentCreate,
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
    session: SessionDependency,
    service: PaymentServiceDependency,
) -> PaymentAcceptedResponse:
    try:
        payment = await service.create_payment(
            session,
            request=request,
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnsafeWebhookUrlError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    return PaymentAcceptedResponse(
        payment_id=payment.id,
        status=payment.status,
        created_at=payment.created_at,
    )


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_payment(
    payment_id: uuid.UUID,
    session: SessionDependency,
    service: PaymentServiceDependency,
) -> PaymentResponse:
    payment = await service.get_payment(session, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return PaymentResponse.model_validate(payment)
