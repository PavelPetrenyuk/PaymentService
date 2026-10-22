from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import Currency
from app.schemas import PaymentCreate
from app.services.payments import canonical_request_hash


def payment_request(*, metadata: dict[str, object] | None = None) -> PaymentCreate:
    return PaymentCreate(
        amount="100.50",
        currency=Currency.RUB,
        description="Order 42",
        metadata=metadata or {},
        webhook_url="https://merchant.example/webhooks/payments",
    )


def test_canonical_hash_ignores_metadata_key_order() -> None:
    first = payment_request(metadata={"order": 42, "customer": "alice"})
    second = payment_request(metadata={"customer": "alice", "order": 42})

    assert canonical_request_hash(first) == canonical_request_hash(second)


def test_money_is_parsed_as_decimal() -> None:
    request = payment_request()

    assert request.amount == Decimal("100.50")
    assert isinstance(request.amount, Decimal)


@pytest.mark.parametrize("amount", ["0", "-1", "1.001"])
def test_invalid_money_is_rejected(amount: str) -> None:
    with pytest.raises(ValidationError):
        PaymentCreate(
            amount=amount,
            currency="RUB",
            description="invalid",
            webhook_url="https://merchant.example/webhook",
        )
