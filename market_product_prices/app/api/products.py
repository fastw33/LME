from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.services.market_product_service import list_latest_prices, list_product_history, list_products


router = APIRouter(prefix="/market", tags=["market-products"])


@router.get("/products")
def get_products(active_only: bool = Query(False)) -> dict:
    return {"items": list_products(active_only=active_only)}


@router.get("/products/{product_id}/history")
def get_product_history(
    product_id: int,
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
) -> dict:
    return {"items": list_product_history(product_id=product_id, desde=desde, hasta=hasta)}


@router.get("/prices/latest")
def get_latest_prices() -> dict:
    return {"items": list_latest_prices()}
