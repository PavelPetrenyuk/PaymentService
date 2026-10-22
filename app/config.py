from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded explicitly from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PAYMENTS_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://payments:payments@postgres:5432/payments"
    rabbitmq_url: str = "amqp://payments:payments@rabbitmq:5672/"

    api_key: SecretStr
    webhook_signing_secret: SecretStr

    webhook_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    webhook_read_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    webhook_retry_second_delay_seconds: int = Field(default=1, ge=1, le=3600)
    webhook_retry_third_delay_seconds: int = Field(default=2, ge=1, le=3600)
    payment_processing_min_delay_seconds: float = Field(default=2.0, ge=0, le=30)
    payment_processing_max_delay_seconds: float = Field(default=5.0, ge=0, le=60)
    outbox_poll_interval_seconds: float = Field(default=0.5, ge=0.05, le=60)
    outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    outbox_lease_seconds: int = Field(default=30, ge=5, le=3600)
    rabbit_publish_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @field_validator("api_key", "webhook_signing_secret")
    @classmethod
    def reject_empty_secrets(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("secret must not be empty")
        return value

    @field_validator("payment_processing_max_delay_seconds")
    @classmethod
    def validate_processing_delay(cls, value: float, info: object) -> float:
        data = getattr(info, "data", {})
        minimum = data.get("payment_processing_min_delay_seconds", 0)
        if value < minimum:
            raise ValueError("maximum processing delay must be greater than or equal to minimum")
        return value

    @property
    def allow_insecure_webhooks(self) -> bool:
        return self.environment != "production"

    def retry_delay_seconds(self, attempt: int) -> int:
        delays = {
            2: self.webhook_retry_second_delay_seconds,
            3: self.webhook_retry_third_delay_seconds,
        }
        try:
            return delays[attempt]
        except KeyError as exc:
            raise ValueError(f"no retry delay configured for attempt {attempt}") from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()
