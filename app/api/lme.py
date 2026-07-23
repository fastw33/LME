from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.lme_service import (
    build_prices_payload,
    create_metal,
    list_history,
    list_metals,
    list_runs,
    run_lme_scrape,
    set_metal_active,
    update_metal,
)


router = APIRouter(prefix="/lme", tags=["lme"])


class LmeMetalPayload(BaseModel):
    metalKey: str | None = None
    nameEs: str | None = None
    nameEn: str | None = None
    slugLme: str | None = None
    urlLme: str | None = None
    isActive: bool | None = None
    displayOrder: int | None = None
    notes: str | None = None


class LmeMetalTogglePayload(BaseModel):
    isActive: bool


@router.get("/prices")
async def get_lme_prices(refresh: bool = Query(False)) -> dict:
    if refresh:
        return await run_lme_scrape(trigger="manual")
    return build_prices_payload(from_cache=True)


@router.post("/scrape")
async def scrape_lme_prices() -> dict:
    return await run_lme_scrape(trigger="manual")


@router.get("/history")
def get_lme_history(
    metal_key: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> dict:
    return {
        "items": list_history(metal_key=metal_key, desde=desde, hasta=hasta),
    }


@router.get("/daily-prices")
def get_lme_daily_prices(
    metal_key: str | None = Query(None),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> dict:
    return {
        "items": list_history(metal_key=metal_key, desde=desde, hasta=hasta),
    }


@router.get("/runs")
def get_lme_runs(limit: int = Query(20, ge=1, le=100)) -> dict:
    return {"items": list_runs(limit=limit)}


@router.get("/metals")
def get_lme_metals(active_only: bool = Query(False)) -> dict:
    return {"items": list_metals(active_only=active_only)}


@router.post("/metals")
def post_lme_metal(payload: LmeMetalPayload) -> dict:
    try:
        return {"item": create_metal(payload.model_dump(exclude_none=True))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/metals/{metal_id}")
def put_lme_metal(metal_id: int, payload: LmeMetalPayload) -> dict:
    try:
        return {"item": update_metal(metal_id, payload.model_dump(exclude_none=True))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/metals/{metal_id}/active")
def patch_lme_metal_active(metal_id: int, payload: LmeMetalTogglePayload) -> dict:
    try:
        return {"item": set_metal_active(metal_id, payload.isActive)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
