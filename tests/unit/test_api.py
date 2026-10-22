import httpx

from app.config import Settings
from app.main import MAX_REQUEST_BODY_BYTES, create_app


def settings() -> Settings:
    return Settings(
        api_key="test-api-key",
        webhook_signing_secret="test-webhook-secret",
        database_url="postgresql+asyncpg://unused:unused@localhost/unused",
        environment="test",
    )


async def test_health_endpoint_requires_api_key() -> None:
    application = create_app(settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/health/live")
        response = await client.get("/health/live", headers={"X-API-Key": "test-api-key"})

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_automatic_documentation_routes_are_disabled() -> None:
    application = create_app(settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        docs = await client.get("/docs")
        schema = await client.get("/openapi.json")

    assert docs.status_code == 404
    assert schema.status_code == 404


async def test_business_endpoint_requires_api_key_before_database_access() -> None:
    application = create_app(settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/payments/{'0' * 32}")

    assert response.status_code == 401


async def test_request_body_limit() -> None:
    application = create_app(settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/payments",
            content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
            headers={"X-API-Key": "test-api-key"},
        )

    assert response.status_code == 413
