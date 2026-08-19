from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "si"}


class Settings:
    app_name: str = os.getenv("APP_NAME", "LME Scraper API")
    app_env: str = os.getenv("APP_ENV", "development")
    app_debug: bool = _bool_env("APP_DEBUG")
    cors_origins: list[str] = _csv_env("CORS_ORIGINS")
    cors_allow_methods: list[str] = _csv_env("CORS_ALLOW_METHODS")
    cors_allow_headers: list[str] = _csv_env("CORS_ALLOW_HEADERS")
    cors_allow_credentials: bool = _bool_env("CORS_ALLOW_CREDENTIALS")
    auth_public_paths: list[str] = _csv_env("AUTH_PUBLIC_PATHS") or ["/api/health"]
    timezone: str = os.getenv("TIMEZONE", "America/Bogota")
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_name: str = os.getenv("DB_NAME", "metal_harvest_lme")
    scraper_headless: bool = os.getenv("SCRAPER_HEADLESS", "true").lower() == "true"
    scraper_timeout_ms: int = int(os.getenv("SCRAPER_TIMEOUT_MS", "45000"))
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    internal_service_key: str = os.getenv("INTERNAL_SERVICE_KEY", "")
    alloy_update_url: str = os.getenv(
        "ALLOY_UPDATE_URL",
        "http://127.0.0.1:4060/api/aleaciones/actualizar-precios",
    )
    alloy_update_internal_service_key: str = os.getenv(
        "ALLOY_UPDATE_INTERNAL_SERVICE_KEY",
        internal_service_key,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
