from app.config import Settings
from app.messaging.topology import build_topology


def test_retry_queues_use_configured_ttl_and_dead_letter_back_to_main() -> None:
    settings = Settings(
        api_key="test-api-key",
        webhook_signing_secret="test-webhook-secret",
        webhook_retry_second_delay_seconds=10,
        webhook_retry_third_delay_seconds=30,
    )

    topology = build_topology(settings)

    assert topology.retry_second_queue.arguments["x-message-ttl"] == 10_000
    assert topology.retry_third_queue.arguments["x-message-ttl"] == 30_000
    assert topology.retry_second_queue.arguments["x-dead-letter-exchange"] == "payments"
    assert topology.main_queue.arguments["x-single-active-consumer"] is True
