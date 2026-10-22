import uuid

from app.integrations.payment_gateway import DeterministicPaymentGateway


async def test_gateway_is_deterministic_and_uses_bounded_delay() -> None:
    delays: list[float] = []

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)

    gateway = DeterministicPaymentGateway(
        min_delay_seconds=2,
        max_delay_seconds=5,
        sleeper=capture_sleep,
    )
    payment_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    first = await gateway.process(payment_id)
    second = await gateway.process(payment_id)

    assert first == second
    assert all(2 <= delay <= 5 for delay in delays)


async def test_gateway_has_approximately_ninety_percent_success_rate() -> None:
    async def no_sleep(_: float) -> None:
        return None

    gateway = DeterministicPaymentGateway(
        min_delay_seconds=0,
        max_delay_seconds=0,
        sleeper=no_sleep,
    )

    results = [await gateway.process(uuid.UUID(int=value)) for value in range(1, 1001)]
    successes = sum(results)

    assert 850 <= successes <= 950
