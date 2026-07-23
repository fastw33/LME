from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    app_name: str = os.getenv("APP_NAME", "LME Scraper API")
    app_env: str = os.getenv("APP_ENV", "development")
    app_debug: bool = os.getenv("APP_DEBUG", "false").lower() == "true"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
