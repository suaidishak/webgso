from functools import lru_cache
from zoneinfo import ZoneInfo
from datetime import datetime

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://gso:gso@localhost:5432/gso"
    jwt_secret: str = "change-me-in-production"
    jwt_ttl_minutes: int = 12 * 60
    # Encrypts plant API keys at rest. Set it once and keep it: changing it makes stored keys unreadable.
    # If empty, a key is derived from JWT_SECRET (fine for local use, not for production).
    plant_key_secret: str = ""
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
    # Polling at fleet scale (100+ plants). At most this many plant calls run at once across the
    # whole process, and at most poll_per_host at once against any one host (several plants can
    # share one host, as the 8 pilots do).
    poll_concurrency: int = 32
    poll_per_host: int = 4
    db_pool_size: int = 20
    db_max_overflow: int = 20
    # Poll now: how long the request waits for a run before handing back a run id to follow.
    poll_now_wait_s: int = 20
    poll_now_max_endpoints: int = 2500
    # Offline demo: when > 0 and the database has no plants, seed this many simulated plants
    # (spread over regions) instead of the 8 pilots. Use with SEED_BASE_URL pointing at mockplants.
    seed_mock_fleet: int = 0
    cors_origins: str = "http://localhost:5173,http://localhost:8080"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now_local() -> datetime:
    """Current Malaysia time as a naive datetime (rev5 timestamps carry no offset)."""
    return datetime.now(tz()).replace(tzinfo=None, microsecond=0)
