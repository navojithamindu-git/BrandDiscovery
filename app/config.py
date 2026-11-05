from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "brand-discovery"
    database_url: str = "sqlite:///./brand_discovery.db"
    meta_graph_version: str = "v23.0"
    meta_graph_url: str = "https://graph.facebook.com"
    meta_access_token: str | None = None
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_login_config_id: str | None = None
    meta_oauth_redirect_uri: str = "http://localhost:8000/auth/meta/callback"
    meta_oauth_state_secret: str | None = None
    meta_token_encryption_key: str | None = None
    meta_token_vault_path: str = ".tokens.enc"
    meta_oauth_state_ttl_seconds: int = 600
    collection_interval_minutes: int = 15
    request_timeout_seconds: float = 30
    max_retries: int = 4
    max_backfill_pages_per_request: int = 100
    log_level: str = "INFO"
    meta_webhook_verify_token: str | None = None
    meta_webhook_app_secret: str | None = None
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

