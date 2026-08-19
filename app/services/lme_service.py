from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request
from datetime import date, datetime, timezone

from app.core.config import get_settings
from app.core.database import get_connection, parse_db_datetime, row_to_dict, utc_now_db
from app.services.lme_scraper import DEFAULT_LME_TARGETS, PRICE_BASIS, LmeMetalTarget, scrape_lme_targets


logger = logging.getLogger(__name__)
_scrape_lock = asyncio.Lock()
_ALLOWED_TRIGGERS = {"manual", "scheduled_07", "scheduled_14", "retry", "test"}


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_")


def _exception_message(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


def _failure_summary(failures: list[dict]) -> str:
    messages = sorted(
        {
            item.get("error_message") or "Error desconocido"
            for item in failures
            if item.get("error_message") or item.get("label")
        }
    )
    return "; ".join(messages[:3])


def _post_alloy_auto_update(url: str) -> dict | None:
    settings = get_settings()
    payload = json.dumps({"refreshRates": True}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    internal_key = settings.alloy_update_internal_service_key.strip()
    if internal_key:
        headers["X-Internal-Service-Key"] = internal_key

    request = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else None


async def _notify_alloy_auto_update() -> None:
    url = get_settings().alloy_update_url.strip()
    if not url:
        return

    try:
        await asyncio.to_thread(_post_alloy_auto_update, url)
    except Exception as exc:
        logger.warning("No fue posible recalcular aleaciones automaticamente: %s", _exception_message(exc))


def _normalize_metal_payload(data: dict, *, partial: bool = False) -> dict:
    cleaned = {
        "metal_key": (data.get("metal_key") or data.get("metalKey") or "").strip(),
        "name_es": (data.get("name_es") or data.get("nameEs") or "").strip(),
        "name_en": (data.get("name_en") or data.get("nameEn") or "").strip(),
        "slug_lme": (data.get("slug_lme") or data.get("slugLme") or "").strip().lower(),
        "url_lme": (data.get("url_lme") or data.get("urlLme") or "").strip(),
        "notes": data.get("notes"),
    }

    if "is_active" in data or "isActive" in data:
        cleaned["is_active"] = 1 if data.get("is_active", data.get("isActive")) else 0
    if "display_order" in data or "displayOrder" in data:
        cleaned["display_order"] = int(data.get("display_order", data.get("displayOrder")) or 100)

    if cleaned["slug_lme"] and not cleaned["metal_key"]:
        cleaned["metal_key"] = f"lme_{_slugify(cleaned['slug_lme'])}"

    required = ["metal_key", "name_es", "name_en", "slug_lme", "url_lme"]
    if not partial:
        missing = [field for field in required if not cleaned.get(field)]
        if missing:
            raise ValueError(f"Faltan campos requeridos: {', '.join(missing)}")

    return cleaned


def _metal_row_to_dict(row) -> dict:
    item = row_to_dict(row)
    if not item:
        return {}
    return {
        "id": item["metal_id"],
        "metalKey": item["metal_key"],
        "nameEs": item["name_es"],
        "nameEn": item["name_en"],
        "slugLme": item["slug_lme"],
        "urlLme": item["url_lme"],
        "isActive": bool(item["is_active"]),
        "displayOrder": item["display_order"],
        "notes": item.get("notes") or "",
        "createdAt": item["created_at"],
        "updatedAt": item["updated_at"],
    }


def list_metals(*, active_only: bool = False) -> list[dict]:
    filters = ["1 = 1"]
    if active_only:
        filters.append("is_active = 1")

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM lme_metals
                WHERE {' AND '.join(filters)}
                ORDER BY display_order ASC, name_es ASC
                """
            )
            rows = cursor.fetchall()
    return [_metal_row_to_dict(row) for row in rows]


def list_active_targets() -> list[LmeMetalTarget]:
    metals = list_metals(active_only=True)
    if not metals:
        return DEFAULT_LME_TARGETS
    return [
        LmeMetalTarget(
            key=metal["metalKey"],
            label=metal["nameEs"],
            slug=metal["slugLme"],
            url=metal["urlLme"],
        )
        for metal in metals
    ]


def create_metal(data: dict) -> dict:
    item = _normalize_metal_payload(data)
    now = utc_now_db()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO lme_metals (
                    metal_key, name_es, name_en, slug_lme, url_lme,
                    is_active, display_order, notes, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item["metal_key"],
                    item["name_es"],
                    item["name_en"],
                    item["slug_lme"],
                    item["url_lme"],
                    item.get("is_active", 1),
                    item.get("display_order", 100),
                    item.get("notes"),
                    now,
                    now,
                ),
            )
            metal_id = cursor.lastrowid
            cursor.execute("SELECT * FROM lme_metals WHERE metal_id = %s", (metal_id,))
            row = cursor.fetchone()
    return _metal_row_to_dict(row)


def update_metal(metal_id: int, data: dict) -> dict:
    item = _normalize_metal_payload(data, partial=True)
    allowed = {
        "metal_key",
        "name_es",
        "name_en",
        "slug_lme",
        "url_lme",
        "is_active",
        "display_order",
        "notes",
    }
    updates = [(key, value) for key, value in item.items() if key in allowed and value != ""]
    if not updates:
        raise ValueError("No hay campos para actualizar.")

    assignments = ", ".join(f"{key} = %s" for key, _ in updates)
    params = [value for _, value in updates]
    params.append(metal_id)

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE lme_metals
                SET {assignments}
                WHERE metal_id = %s
                """,
                params,
            )
            cursor.execute("SELECT * FROM lme_metals WHERE metal_id = %s", (metal_id,))
            row = cursor.fetchone()
    if not row:
        raise LookupError("Metal no encontrado.")
    return _metal_row_to_dict(row)


def set_metal_active(metal_id: int, is_active: bool) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE lme_metals
                SET is_active = %s
                WHERE metal_id = %s
                """,
                (1 if is_active else 0, metal_id),
            )
            cursor.execute("SELECT * FROM lme_metals WHERE metal_id = %s", (metal_id,))
            row = cursor.fetchone()
    if not row:
        raise LookupError("Metal no encontrado.")
    return _metal_row_to_dict(row)


def _insert_run(trigger: str) -> int:
    trigger_type = trigger if trigger in _ALLOWED_TRIGGERS else "manual"
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO lme_scrape_runs (trigger_type, status, started_at)
                VALUES (%s, %s, %s)
                """,
                (trigger_type, "running", utc_now_db()),
            )
            return int(cursor.lastrowid)


def _finish_run(run_id: int, status: str, message: str = "", rows_ok: int = 0, rows_failed: int = 0) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE lme_scrape_runs
                SET status = %s,
                    finished_at = %s,
                    rows_ok = %s,
                    rows_failed = %s,
                    message = %s
                WHERE run_id = %s
                """,
                (status, utc_now_db(), rows_ok, rows_failed, message, run_id),
            )


def _insert_scraped_price(run_id: int, item: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT metal_id FROM lme_metals WHERE metal_key = %s",
                (item["metal_key"],),
            )
            metal = cursor.fetchone()
            if not metal:
                return

            cursor.execute(
                """
                INSERT INTO lme_scraped_prices (
                    run_id, metal_id, source_name, source_url, price, currency,
                    unit, variation_percent, price_basis, data_timestamp,
                    scraped_at, status, error_message, raw_excerpt
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    source_name = VALUES(source_name),
                    source_url = VALUES(source_url),
                    price = VALUES(price),
                    currency = VALUES(currency),
                    unit = VALUES(unit),
                    variation_percent = VALUES(variation_percent),
                    data_timestamp = VALUES(data_timestamp),
                    scraped_at = VALUES(scraped_at),
                    status = VALUES(status),
                    error_message = VALUES(error_message),
                    raw_excerpt = VALUES(raw_excerpt)
                """,
                (
                    run_id,
                    metal["metal_id"],
                    item.get("source_name", "LME.com"),
                    item.get("source_url", item["url"]),
                    item.get("price"),
                    item.get("currency", "USD"),
                    item.get("unit", "tonelada métrica"),
                    item.get("change_percent"),
                    item.get("price_basis", PRICE_BASIS),
                    parse_db_datetime(item.get("market_timestamp")),
                    parse_db_datetime(item["fetched_at"]),
                    item["status"],
                    item.get("error_message"),
                    item.get("raw_excerpt"),
                ),
            )


def _latest_success_by_metal() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.price_id,
                       p.run_id,
                       m.metal_key,
                       m.name_es AS label,
                       m.slug_lme AS slug,
                       m.url_lme AS url,
                       p.price,
                       p.variation_percent AS change_percent,
                       p.currency,
                       p.unit,
                       p.price_basis,
                       p.source_name,
                       p.source_url,
                       p.data_timestamp AS market_timestamp,
                       p.scraped_at AS fetched_at,
                       p.status,
                       p.error_message,
                       p.raw_excerpt
                FROM lme_scraped_prices p
                INNER JOIN lme_metals m ON m.metal_id = p.metal_id
                WHERE p.status = 'ok'
                ORDER BY
                    DATE(p.scraped_at) DESC,
                    CASE WHEN p.source_name = 'LME.com' THEN 0 ELSE 1 END ASC,
                    p.scraped_at DESC,
                    p.price_id DESC
                """
            )
            rows = cursor.fetchall()
    for row in rows:
        item = row_to_dict(row)
        if item and item["metal_key"] not in latest:
            latest[item["metal_key"]] = item
    return latest


def _runs_today_count() -> int:
    today = datetime.now(timezone.utc).date()
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM lme_scrape_runs
                WHERE DATE(started_at) = %s
                  AND status IN ('running', 'ok', 'partial')
                """,
                (today,),
            )
            row = cursor.fetchone()
    return int(row["total"] if row else 0)


def build_prices_payload(*, from_cache: bool = False, message: str = "", error: str = "") -> dict:
    latest = _latest_success_by_metal()
    targets = list_active_targets()
    metals = []
    fetched_dates = []
    market_dates = []
    for target in targets:
        item = latest.get(target.key)
        metals.append(
            {
                "key": target.key,
                "label": target.label,
                "price": item["price"] if item else None,
                "changePercent": item["change_percent"] if item else None,
                "url": target.url,
                "source": item.get("source_name") if item else None,
                "sourceUrl": item.get("source_url") if item else None,
                "marketTimestamp": item.get("market_timestamp") if item else None,
                "fetchedAt": item.get("fetched_at") if item else None,
                "status": "ok" if item else "missing",
            }
        )
        if item and item.get("fetched_at"):
            fetched_dates.append(item["fetched_at"])
        if item and item.get("market_timestamp"):
            market_dates.append(item["market_timestamp"])

    fetched_at = max(fetched_dates) if fetched_dates else None
    market_timestamp = max(market_dates) if market_dates else fetched_at
    source_names = sorted(
        {
            item.get("source_name")
            for item in latest.values()
            if item.get("source_name")
        }
    )
    return {
        "metals": metals,
        "source": " + ".join(source_names) if source_names else "LME.com",
        "priceBasis": "Precio origen por fuente. LME usa 3-month Closing Price; SMM usa 1# Tungsten bar convertido de USD/kg a USD/t.",
        "currency": "USD",
        "unit": "mt",
        "unitLabel": "tonelada métrica",
        "marketTimestamp": market_timestamp,
        "fetchedAt": fetched_at,
        "fromCache": False,
        "queriesUsedToday": _runs_today_count(),
        "queryLimit": None,
        "limitReached": False,
        "autoRefreshAvailable": True,
        "error": error,
        "message": message,
    }


async def run_lme_scrape(trigger: str = "manual") -> dict:
    if _scrape_lock.locked():
        return build_prices_payload(
            message="Ya hay una captura LME en ejecución. Se muestran los últimos datos guardados en MariaDB.",
        )

    async with _scrape_lock:
        run_id = _insert_run(trigger)
        try:
            results = await scrape_lme_targets(list_active_targets())
            for item in results:
                _insert_scraped_price(run_id, item)

            failures = [item for item in results if item.get("status") != "ok"]
            rows_ok = len(results) - len(failures)
            rows_failed = len(failures)
            if len(failures) == len(results):
                detail = _failure_summary(failures)
                message = (
                    f"No se pudieron actualizar precios. {detail}"
                    if detail
                    else "No se pudieron actualizar precios."
                )
                _finish_run(run_id, "failed", message, rows_ok, rows_failed)
                return build_prices_payload(message=message, error=message)

            if rows_ok:
                await _notify_alloy_auto_update()

            message = (
                "Captura LME completada."
                if not failures
                else f"Actualización parcial: {len(failures)} metal(es) fallaron. {_failure_summary(failures)}"
            )
            _finish_run(run_id, "ok" if not failures else "partial", message, rows_ok, rows_failed)
            return build_prices_payload(message=message)
        except Exception as exc:
            message = _exception_message(exc)
            _finish_run(run_id, "failed", message)
            return build_prices_payload(message=message, error=message)


def list_history(metal_key: str | None = None, desde: date | None = None, hasta: date | None = None) -> list[dict]:
    filters = ["p.status = 'ok'"]
    params: list[object] = []
    if metal_key:
        filters.append("m.metal_key = %s")
        params.append(metal_key)
    if desde:
        filters.append("DATE(p.scraped_at) >= %s")
        params.append(desde)
    if hasta:
        filters.append("DATE(p.scraped_at) <= %s")
        params.append(hasta)

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DATE(p.scraped_at) AS scrape_date,
                       DATE(p.data_timestamp) AS market_date,
                       m.metal_key,
                       m.name_es AS label,
                       m.slug_lme AS slug,
                       p.price,
                       p.variation_percent AS change_percent,
                       p.currency,
                       p.unit,
                       p.price_basis,
                       p.source_name,
                       p.source_url AS url,
                       p.data_timestamp AS market_timestamp,
                       p.scraped_at AS fetched_at,
                       p.run_id,
                       p.created_at
                FROM lme_scraped_prices p
                INNER JOIN lme_metals m ON m.metal_id = p.metal_id
                WHERE {' AND '.join(filters)}
                ORDER BY p.scraped_at ASC, m.display_order ASC, m.name_es ASC
                """,
                params,
            )
            rows = cursor.fetchall()
    return [row_to_dict(row) for row in rows if row]


def list_runs(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id AS id, trigger_type AS trigger, status,
                       started_at, finished_at, rows_ok, rows_failed, message
                FROM lme_scrape_runs
                ORDER BY started_at DESC, run_id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
    return [row_to_dict(row) for row in rows if row]
