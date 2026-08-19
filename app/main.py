from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.lme import router as lme_router
from app.core.auth import auth_http_middleware
from app.core.config import get_settings
from app.core.database import ensure_database
from app.services.lme_service import run_lme_scrape


logger = logging.getLogger(__name__)
settings = get_settings()
scheduler = AsyncIOScheduler(timezone=settings.timezone)


async def scheduled_scrape_07() -> None:
    logger.info("Ejecutando scraping LME programado 07:00")
    await run_lme_scrape(trigger="scheduled_07")


async def scheduled_scrape_14() -> None:
    logger.info("Ejecutando scraping LME programado 14:00")
    await run_lme_scrape(trigger="scheduled_14")


app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

app.middleware("http")(auth_http_middleware)

app.include_router(lme_router, prefix="/api")


@app.on_event("startup")
async def startup() -> None:
    ensure_database()
    if not scheduler.running:
        scheduler.add_job(
            scheduled_scrape_07,
            CronTrigger(hour=7, minute=0, timezone=settings.timezone),
            id="lme_scrape_07",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            scheduled_scrape_14,
            CronTrigger(hour=14, minute=0, timezone=settings.timezone),
            id="lme_scrape_14",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "service": settings.app_name,
        "timezone": settings.timezone,
        "scheduled_runs": ["07:00", "14:00"],
    }
