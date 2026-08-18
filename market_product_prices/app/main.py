from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import market_ocr, products
from app.core.config import get_settings
from app.core.database import check_connection


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_ocr.router, prefix="/api")
app.include_router(products.router, prefix="/api")


@app.get("/api/market/health", tags=["market"])
def health() -> dict:
    return {
        "ok": True,
        "service": settings.app_name,
        "database": check_connection(),
        "ocrWorkerUrl": settings.ocr_worker_url,
    }
