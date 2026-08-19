from __future__ import annotations

import asyncio
import html
from html.parser import HTMLParser
import re
import sys
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import get_settings


PRICE_BASIS = "3-month Closing Price (day-delayed)"
WESTMETALL_LME_BASIS = "Official LME 3-month price via Westmetall"
WESTMETALL_MARKET_DATA_URL = "https://www.westmetall.com/en/markdaten.php"
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


class _TableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            value = _collapse_text(" ".join(self._cell))
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


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


def _clean_search_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return _collapse_text(value)


def _diagnostic_excerpt(text: str, target: LmeMetalTarget, *, limit: int = 800) -> str | None:
    normalized = _clean_search_text(text)
    if not normalized:
        return None

    lowered = normalized.lower()
    markers = [
        "3-month closing price",
        "closing price",
        f"lme {target.slug}".lower(),
        target.slug.lower(),
    ]
    positions = [lowered.find(marker) for marker in markers if lowered.find(marker) >= 0]
    if positions:
        center = min(positions)
        start = max(0, center - limit // 2)
        end = min(len(normalized), start + limit)
        return normalized[start:end]
    return normalized[:limit]


def _extract_lme_price_block(text: str, target: LmeMetalTarget) -> tuple[float, float | None, str]:
    normalized = _clean_search_text(text)
    if not normalized:
        raise LmeScrapeError("La página no devolvió texto visible.")

    lowered = normalized.lower()
    if "just a moment" in lowered or "enable javascript and cookies" in lowered:
        raise LmeScrapeError("LME devolvió una página de verificación/bloqueo.")

    metal_title = f"LME {target.slug.replace('-', ' ').title()}"
    metal_markers = [metal_title.lower(), target.slug.lower()]
    price_pattern = re.compile(
        r"([0-9]{3,6}(?:,[0-9]{3})?(?:\.[0-9]+)?)\s+([+-]?[0-9]+(?:\.[0-9]+)?)%",
    )
    blocks: list[str] = []

    for marker in ("3-month closing price", "closing price"):
        search_from = 0
        while True:
            basis_index = lowered.find(marker, search_from)
            if basis_index < 0:
                break
            start = max(0, basis_index - 260)
            end = min(len(normalized), basis_index + 180)
            blocks.append(normalized[start:end])
            search_from = basis_index + len(marker)

    for match in price_pattern.finditer(normalized):
        start = max(0, match.start() - 220)
        end = min(len(normalized), match.end() + 220)
        block = normalized[start:end]
        block_lowered = block.lower()
        if any(marker in block_lowered for marker in metal_markers):
            blocks.append(block)

    seen: set[str] = set()
    for excerpt in blocks:
        if excerpt in seen:
            continue
        seen.add(excerpt)
        excerpt_lowered = excerpt.lower()
        if not any(marker in excerpt_lowered for marker in metal_markers):
            continue
        match = price_pattern.search(excerpt)
        if not match:
            continue
        price = _parse_number(match.group(1))
        change_percent = float(match.group(2))
        if price <= 0:
            continue
        return price, change_percent, excerpt

    raise LmeScrapeError("No se encontró el bloque 3-month Closing Price.")


def parse_lme_page_text(text: str, target: LmeMetalTarget) -> dict:
    price, change_percent, excerpt = _extract_lme_price_block(text, target)

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


def _failed_lme_result(target: LmeMetalTarget, exc: Exception, raw_text: str = "") -> dict:
    return {
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
        "raw_excerpt": _diagnostic_excerpt(raw_text, target),
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
            raw_text = ""
            try:
                await page.goto(target.url, wait_until="domcontentloaded", timeout=settings.scraper_timeout_ms)
                await _accept_cookies_if_present(page)
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except PlaywrightTimeoutError:
                    pass
                try:
                    await page.wait_for_function(
                        "() => /3-month\\s+closing\\s+price/i.test(document.body?.innerText || document.body?.textContent || '')",
                        timeout=8000,
                    )
                except PlaywrightTimeoutError:
                    pass

                raw_parts = [
                    await page.title(),
                    await page.locator("body").inner_text(timeout=settings.scraper_timeout_ms),
                    await page.evaluate(
                        "() => Array.from(document.querySelectorAll('meta[name=\"description\"], meta[property=\"og:description\"]')).map((el) => el.content).join(' ')"
                    ),
                    await page.evaluate("() => document.body?.textContent || ''"),
                    await page.content(),
                ]
                raw_text = " ".join(part for part in raw_parts if part)
                results.append(parse_lme_page_text(raw_text, target))
            except Exception as exc:
                results.append(_failed_lme_result(target, exc, raw_text))

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


def _parse_westmetall_market_date(page_text: str) -> str | None:
    months = {
        "january": "01",
        "february": "02",
        "march": "03",
        "april": "04",
        "may": "05",
        "june": "06",
        "july": "07",
        "august": "08",
        "september": "09",
        "october": "10",
        "november": "11",
        "december": "12",
    }
    match = re.search(r"(\d{1,2})\.\s+([A-Za-z]+)\s+(\d{4})", page_text)
    if not match:
        return None
    month = months.get(match.group(2).lower())
    if not month:
        return None
    return f"{match.group(3)}-{month}-{int(match.group(1)):02d}T00:00:00+00:00"


def scrape_westmetall_lme_targets(targets: list[LmeMetalTarget]) -> list[dict]:
    target_names = {
        "copper": "Copper",
        "lead": "Lead",
        "nickel": "Nickel",
        "tin": "Tin",
        "zinc": "Zinc",
        "aluminium": "Aluminium",
    }
    request = urllib.request.Request(
        WESTMETALL_MARKET_DATA_URL,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        raw_html = response.read().decode("utf-8", errors="replace")

    parser = _TableTextParser()
    parser.feed(raw_html)
    page_text = _clean_search_text(raw_html)
    market_timestamp = _parse_westmetall_market_date(page_text)
    results: list[dict] = []

    for target in targets:
        source_label = target_names.get(target.slug.lower())
        row = next(
            (
                item
                for item in parser.rows
                if source_label
                and len(item) >= 3
                and item[0].strip().lower() == source_label.lower()
            ),
            None,
        )
        if not row:
            results.append(_failed_lme_result(target, LmeScrapeError("Westmetall no devolvió el metal."), page_text))
            continue

        try:
            price = _parse_number(row[2])
            if price <= 0:
                raise LmeScrapeError("Westmetall devolvió precio no válido.")
        except Exception as exc:
            results.append(_failed_lme_result(target, exc, " | ".join(row)))
            continue

        results.append(
            {
                "metal_key": target.key,
                "label": target.label,
                "slug": target.slug,
                "url": target.url,
                "price": price,
                "change_percent": None,
                "currency": "USD",
                "unit": "mt",
                "price_basis": WESTMETALL_LME_BASIS,
                "source_name": "Westmetall",
                "source_url": WESTMETALL_MARKET_DATA_URL,
                "market_timestamp": market_timestamp,
                "fetched_at": _now_utc_iso(),
                "status": "ok",
                "error_message": None,
                "raw_excerpt": " | ".join(row[:3]),
            }
        )

    return results


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
        failed_targets = [
            target
            for target in lme_targets
            if any(item.get("metal_key") == target.key and item.get("status") != "ok" for item in results)
        ]
        if failed_targets:
            try:
                fallback_by_key = {
                    item["metal_key"]: item
                    for item in scrape_westmetall_lme_targets(failed_targets)
                    if item.get("status") == "ok"
                }
            except Exception:
                fallback_by_key = {}
            if fallback_by_key:
                results = [
                    fallback_by_key.get(item["metal_key"], item)
                    if item.get("status") != "ok"
                    else item
                    for item in results
                ]
    results.extend(scrape_smm_tungsten_target(target) for target in smm_targets)
    return results
