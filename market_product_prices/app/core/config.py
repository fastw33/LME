from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    app_name: str = os.getenv("APP_NAME", "Market Product Prices API")
    app_debug: bool = os.getenv("APP_DEBUG", "false").lower() == "true"
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "root")
    db_password: str = os.getenv("DB_PASSWORD", "")
    market_db_name: str = os.getenv("MARKET_DB_NAME", "market_product_prices")
    ocr_worker_url: str = os.getenv("OCR_WORKER_URL", "http://127.0.0.1:8020")
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    price_change_review_percent: float = float(os.getenv("MARKET_PRICE_CHANGE_REVIEW_PERCENT", "10"))
    min_ocr_confidence: float = float(os.getenv("MARKET_MIN_OCR_CONFIDENCE", "70"))
    upload_root: Path = Path(os.getenv("MARKET_UPLOAD_ROOT", str(PROJECT_ROOT / "uploads")))


@lru_cache
def get_settings() -> Settings:
    return Settings()
