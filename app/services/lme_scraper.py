from __future__ import annotations

import asyncio
import re
import sys
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import get_settings


PRICE_BASIS = "3-month Closing Price (day-delayed)"
SMM_TUNGSTEN_BASIS = "SMM 1# Tungsten bar, converted from USD/kg to USD/t"


@dataclass(frozen=True)
class LmeMetalTarget:
    key: str
    label: str
    slug: str
    url: str


DEFAULT_LME_TARGETS = [
    LmeMetalTarget(
        key="lme_lead",
        label="Plomo LME",
        slug="lead",
        url="https://www.lme.com/metals/non-ferrous/lme-lead#Summary",
    ),
    LmeMetalTarget(
        key="lme_nickel",
        label="Níquel LME",
        slug="nickel",
        url="https://www.lme.com/metals/non-ferrous/lme-nickel#Summary",
    ),
    LmeMetalTarget(
        key="lme_copper",
        label="Cobre LME",
        slug="copper",
        url="https://www.lme.com/metals/non-ferrous/lme-copper#Overview",
    ),
    LmeMetalTarget(
        key="lme_tin",
        label="Estaño LME",
        slug="tin",
        url="https://www.lme.com/metals/non-ferrous/lme-tin#Summary",
    ),
    LmeMetalTarget(
        key="lme_zinc",
        label="Zinc LME",
        slug="zinc",
        url="https://www.lme.com/metals/non-ferrous/lme-zinc#Summary",
    ),
    LmeMetalTarget(
        key="smm_tungsten",
        label="Tungsteno SMM",
        slug="tungsten",
        url="https://www.metal.com/es/tungsten#Tungsteno",
    ),
]


class LmeScrapeError(RuntimeError):
    pass


def _collapse_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _parse_number(value: str) -> float:
    return float(value.replace(",", ""))


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _target_is_smm(target: LmeMetalTarget) -> bool:
    return "metal.com" in target.url.lower() or target.key.startswith("smm_")


def _exception_message(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


def parse_lme_page_text(text: str, target: LmeMetalTarget) -> dict:
    normalized = _collapse_text(text)
    if not normalized:
        raise LmeScrapeError("La página no devolvió texto visible.")

    lowered = normalized.lower()
    if "just a moment" in lowered or "enable javascript and cookies" in lowered:
        raise LmeScrapeError("LME devolvió una página de verificación/bloqueo.")

    metal_title = f"LME {target.slug.replace('-', ' ').title()}"
    title_index = lowered.find(metal_title.lower())
    basis_index = lowered.find("3-month closing price", title_index if title_index >= 0 else 0)
    if basis_index < 0:
        raise LmeScrapeError("No se encontró el bloque 3-month Closing Price.")

    start = max(0, basis_index - 180)
    end = min(len(normalized), basis_index + 140)
    excerpt = normalized[start:end]

    match = re.search(
        r"([0-9]{3,6}(?:,[0-9]{3})?(?:\.[0-9]+)?)\s+([+-]?[0-9]+(?:\.[0-9]+)?)%",
        excerpt,
    )
    if not match:
        raise LmeScrapeError("No se pudo leer precio y variación porcentual.")

    price = _parse_number(match.group(1))
    change_percent = float(match.group(2))
    if price <= 0:
        raise LmeScrapeError("El precio leído no es válido.")

    return {
        "metal_key": target.key,
        "label": target.label,
        "slug": target.slug,
        "url": target.url,
        "price": price,
        "change_percent": change_percent,
        "currency": "USD",
        "unit": "mt",
        "price_basis": PRICE_BASIS,
        "source_name": "LME.com",
        "source_url": target.url,
        "market_timestamp": None,
        "fetched_at": _now_utc_iso(),
        "status": "ok",
        "error_message": None,
        "raw_excerpt": excerpt,
    }


async def _accept_cookies_if_present(page) -> None:
    selectors = [
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('I accept')",
        "button:has-text('Aceptar')",
        "#onetrust-accept-btn-handler",
    ]
    for selector in selectors:
        try:
            button = page.locator(selector).first
            if await button.count():
                await button.click(timeout=1500)
                return
        except Exception:
            continue


async def _scrape_lme_targets_from_lme_com_async(targets: list[LmeMetalTarget]) -> list[dict]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    settings = get_settings()
    results: list[dict] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.scraper_headless)
        context = await browser.new_context(
            locale="en-GB",
            timezone_id=settings.timezone,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        page.set_default_timeout(settings.scraper_timeout_ms)

        for target in targets:
            try:
                await page.goto(target.url, wait_until="domcontentloaded", timeout=settings.scraper_timeout_ms)
                await _accept_cookies_if_present(page)
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                text = await page.locator("body").inner_text(timeout=settings.scraper_timeout_ms)
                results.append(parse_lme_page_text(text, target))
            except Exception as exc:
                results.append(
                    {
                        "metal_key": target.key,
                        "label": target.label,
                        "slug": target.slug,
                        "url": target.url,
                        "price": None,
                        "change_percent": None,
                        "currency": "USD",
                        "unit": "mt",
                        "price_basis": PRICE_BASIS,
                        "source_name": "LME.com",
                        "source_url": target.url,
                        "market_timestamp": None,
                        "fetched_at": _now_utc_iso(),
                        "status": "failed",
                        "error_message": _exception_message(exc),
                        "raw_excerpt": None,
                    }
                )

        await context.close()
        await browser.close()

    return results


def _scrape_lme_targets_from_lme_com_thread(targets: list[LmeMetalTarget]) -> list[dict]:
    if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_scrape_lme_targets_from_lme_com_async(targets))
    finally:
        asyncio.set_event_loop(None)
        loop.close()


async def scrape_lme_targets_from_lme_com(targets: list[LmeMetalTarget]) -> list[dict]:
    if sys.platform == "win32":
        return await asyncio.to_thread(_scrape_lme_targets_from_lme_com_thread, list(targets))
    return await _scrape_lme_targets_from_lme_com_async(targets)


def scrape_smm_tungsten_target(target: LmeMetalTarget) -> dict:
    endpoint = "https://platform.metal.com/spotoverseascenter/v1/prices/product_list"
    query = urllib.parse.urlencode({"second_name": "tungsten", "currency_type": 2})
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers={
            "Accept": "application/json",
            "Referer": target.url,
            "Source-Type": "pc",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if payload.get("code") != 0:
            raise LmeScrapeError(payload.get("msg") or "SMM no devolvió datos válidos.")

        categories = payload.get("data", {}).get("category_list") or []
        products = [
            product
            for category in categories
            for product in category.get("products", [])
        ]
        product = next(
            (
                item
                for item in products
                if item.get("product_id") == "201102250208"
                or item.get("product_code") == "SMM-WU-WB-001"
            ),
            products[0] if products else None,
        )
        if not product:
            raise LmeScrapeError("SMM no devolvió productos de Tungsteno.")

        newest = product.get("newest_price") or {}
        average_usd_kg = float(newest.get("average") or 0)
        if average_usd_kg <= 0:
            raise LmeScrapeError("SMM no devolvió precio promedio válido.")

        change_text = str(newest.get("change_rate_percent") or "").replace("%", "")
        change_percent = float(change_text) if change_text else None
        market_date = newest.get("renew_date")
        market_timestamp = f"{market_date}T00:00:00+00:00" if market_date else None
        price_usd_t = average_usd_kg * 1000
        raw_excerpt = (
            f"{product.get('product_name')} | "
            f"{average_usd_kg} {product.get('unit')} | "
            f"low {newest.get('low')} high {newest.get('high')} | "
            f"date {newest.get('renew_date')}"
        )

        return {
            "metal_key": target.key,
            "label": target.label,
            "slug": target.slug,
            "url": target.url,
            "price": price_usd_t,
            "change_percent": change_percent,
            "currency": "USD",
            "unit": "mt",
            "price_basis": SMM_TUNGSTEN_BASIS,
            "source_name": "SMM",
            "source_url": target.url,
            "market_timestamp": market_timestamp,
            "fetched_at": _now_utc_iso(),
            "status": "ok",
            "error_message": None,
            "raw_excerpt": raw_excerpt,
        }
    except Exception as exc:
        return {
            "metal_key": target.key,
            "label": target.label,
            "slug": target.slug,
            "url": target.url,
            "price": None,
            "change_percent": None,
            "currency": "USD",
            "unit": "mt",
            "price_basis": SMM_TUNGSTEN_BASIS,
            "source_name": "SMM",
            "source_url": target.url,
            "market_timestamp": None,
            "fetched_at": _now_utc_iso(),
            "status": "failed",
            "error_message": _exception_message(exc),
            "raw_excerpt": None,
        }


async def scrape_lme_targets(targets: list[LmeMetalTarget] | None = None) -> list[dict]:
    selected_targets = targets or DEFAULT_LME_TARGETS
    if not selected_targets:
        raise LmeScrapeError("No hay metales activos para capturar.")

    lme_targets = [target for target in selected_targets if not _target_is_smm(target)]
    smm_targets = [target for target in selected_targets if _target_is_smm(target)]

    results: list[dict] = []
    if lme_targets:
        results.extend(await scrape_lme_targets_from_lme_com(lme_targets))
    results.extend(scrape_smm_tungsten_target(target) for target in smm_targets)
    return results
