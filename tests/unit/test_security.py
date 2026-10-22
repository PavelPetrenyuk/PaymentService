import pytest

from app.security import UnsafeWebhookUrlError, WebhookUrlPolicy, sign_webhook


async def public_resolver(_: str, __: int) -> list[str]:
    return ["93.184.216.34"]


async def private_resolver(_: str, __: int) -> list[str]:
    return ["127.0.0.1"]


async def mixed_resolver(_: str, __: int) -> list[str]:
    return ["93.184.216.34", "10.0.0.1"]


async def test_webhook_policy_accepts_public_https() -> None:
    policy = WebhookUrlPolicy(allow_http=False, resolver=public_resolver)

    await policy.validate("https://merchant.example/webhook")


@pytest.mark.parametrize(
    ("url", "resolver"),
    [
        ("http://merchant.example/webhook", public_resolver),
        ("https://user:password@merchant.example/webhook", public_resolver),
        ("https://localhost/webhook", private_resolver),
        ("https://merchant.example/webhook", mixed_resolver),
    ],
)
async def test_webhook_policy_rejects_unsafe_destinations(url: str, resolver: object) -> None:
    policy = WebhookUrlPolicy(allow_http=False, resolver=resolver)  # type: ignore[arg-type]

    with pytest.raises(UnsafeWebhookUrlError):
        await policy.validate(url)


def test_webhook_signature_is_stable() -> None:
    signature = sign_webhook("secret", 1_700_000_000, b'{"status":"succeeded"}')

    assert signature == "sha256=fdf113be08000b8ee3e8c98650a1e44a19ee21c5ce94be02fa784818da176dd9"
