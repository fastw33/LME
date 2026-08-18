from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import get_settings


@dataclass
class ValidationResult:
    product_id: int | None
    suggested_product_id: int | None
    requires_review: bool
    review_reason: str
    validation_status: str
    validation_notes: str


def normalize_product_name(value: str | None) -> str:
    normalized = (value or "").upper()
    normalized = normalized.replace("1 #", "1#")
    normalized = re.sub(r"[^A-Z0-9#]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def product_key_from_name(value: str) -> str:
    normalized = normalize_product_name(value).lower()
    key = re.sub(r"[^a-z0-9#]+", "_", normalized)
    key = key.replace("#", "num")
    return key.strip("_") or "market_product"


def validate_extracted_item(conn, item: dict) -> ValidationResult:
    settings = get_settings()
    normalized = normalize_product_name(item.get("nameNormalized") or item.get("name"))
    if not normalized or item.get("price") is None or not item.get("date"):
        return ValidationResult(None, None, True, "missing_required_field", "pending_review", "Faltan campos obligatorios.")

    product = _find_product_by_normalized(conn, normalized)
    if not product:
        return ValidationResult(None, None, True, "new_product", "pending_review", "Producto nuevo pendiente de aprobacion.")

    confidence = item.get("confidence")
    if confidence is not None and float(confidence) < settings.min_ocr_confidence:
        return ValidationResult(product["product_id"], product["product_id"], True, "low_confidence", "pending_review", "Confianza OCR baja.")

    price = float(item.get("price"))
    high = item.get("high")
    low = item.get("low")
    if high is not None and price > float(high):
        return ValidationResult(product["product_id"], product["product_id"], True, "price_above_high", "pending_review", "Precio por encima del high.")
    if low is not None and price < float(low):
        return ValidationResult(product["product_id"], product["product_id"], True, "price_below_low", "pending_review", "Precio por debajo del low.")

    latest = _latest_approved_price(conn, product["product_id"])
    if latest and latest.get("price"):
        previous = float(latest["price"])
        if previous > 0:
            change = abs((price - previous) / previous) * 100
            if change > settings.price_change_review_percent:
                return ValidationResult(
                    product["product_id"],
                    product["product_id"],
                    True,
                    "price_change_too_high",
                    "pending_review",
                    f"Cambio de precio {change:.2f}% supera el limite.",
                )

    return ValidationResult(product["product_id"], product["product_id"], False, "none", "valid", "")


def _find_product_by_normalized(conn, normalized: str) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM products
            WHERE normalized_name = %s
              AND is_active = 1
            LIMIT 1
            """,
            (normalized,),
        )
        product = cursor.fetchone()
        if product:
            return product

        cursor.execute(
            """
            SELECT p.*
            FROM product_aliases a
            INNER JOIN products p ON p.product_id = a.product_id
            WHERE a.normalized_alias = %s
              AND p.is_active = 1
            LIMIT 1
            """,
            (normalized,),
        )
        return cursor.fetchone()


def _latest_approved_price(conn, product_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM price_history
            WHERE product_id = %s
              AND status = 'approved'
            ORDER BY observed_date DESC, price_id DESC
            LIMIT 1
            """,
            (product_id,),
        )
        return cursor.fetchone()
