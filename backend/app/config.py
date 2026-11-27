from functools import lru_cache
from zoneinfo import ZoneInfo
from datetime import datetime

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://gso:gso@localhost:5432/gso"
    jwt_secret: str = "change-me-in-production"
    jwt_ttl_minutes: int = 12 * 60
    # Bootstrap admin, created on first start if no gso_admin exists.
    admin_email: str = "admin@gso.local"
    admin_password: str = "admin12345"
    # Base URL used when seeding the first 8 plants. Point it at the mock server for offline demos.
    seed_base_url: str = "https://ws3.gedine.work"
    timezone: str = "Asia/Kuala_Lumpur"
    scheduler_tick_seconds: int = 30
    # A due run older than this is skipped instead of executed late (e.g. after downtime).
    scheduler_catchup_minutes: int = 10
    verify_tls: bool = True
    cors_origins: str = "http://localhost:5173,http://localhost:8080"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now_local() -> datetime:
    """Current Malaysia time as a naive datetime (rev5 timestamps carry no offset)."""
    return datetime.now(tz()).replace(tzinfo=None, microsecond=0)
