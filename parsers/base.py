import asyncio
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator

from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from config import SCRAPER_DELAY_MIN, SCRAPER_DELAY_MAX, SCRAPER_HEADLESS

logger = logging.getLogger(__name__)


@dataclass
class RawListing:
    """Normalised container for a single scraped property listing."""
    source: str
    source_id: str
    url: str
    project_name: str | None = None
    developer: str | None = None
    unit_number: str | None = None
    district: str | None = None
    subdistrict: str | None = None
    lat: float | None = None
    lon: float | None = None
    property_type: str | None = None    # condo|villa|house|townhouse|land
    ownership_type: str | None = None   # freehold|leasehold|unknown
    leasehold_years: int | None = None
    title_deed: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    area_sqm: float | None = None
    land_sqm: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    price_thb: float | None = None
    price_per_sqm_thb: float | None = None
    cam_fee_per_sqm: float | None = None
    sinking_fund_per_sqm: float | None = None
    furniture_package_thb: float | None = None

    # Rental
    monthly_rent_thb: float | None = None
    rental_type: str | None = None             # short_term | long_term | unknown
    rental_yield_claimed: float | None = None
    rental_program: bool | None = None
    rental_pool_split: float | None = None     # investor share 0–1
    has_hotel_license: bool | None = None

    # Features
    has_pool: bool | None = None
    has_garden: bool | None = None
    has_gym: bool | None = None
    parking_spaces: int | None = None

    # Status
    occupancy_status: str | None = None        # vacant | rented | owner_occupied
    is_off_plan: bool | None = None
    completion_date: datetime | None = None
    year_built: int | None = None

    # Liquidity
    days_on_market: int | None = None
    price_drop_count: int | None = None
    zone_type: str | None = None               # tourist | residential | mixed

    raw_data: dict = field(default_factory=dict)
    scraped_at: datetime = field(default_factory=datetime.utcnow)


class BaseParser(ABC):
    """Abstract scraper — subclass per data source."""

    SOURCE: str = ""

    BROWSER_ARGS = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
    ]

    # Headers to appear as a regular browser
    EXTRA_HEADERS = {
        "Accept-Language": "en-US,en;q=0.9,th;q=0.8",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "DNT": "1",
    }

    def __init__(self) -> None:
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._playwright = None

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> "BaseParser":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=SCRAPER_HEADLESS,
            args=self.BROWSER_ARGS,
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            extra_http_headers=self.EXTRA_HEADERS,
            locale="en-US",
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    async def _new_page(self) -> Page:
        page = await self._context.new_page()
        page.set_default_timeout(30_000)
        return page

    async def _goto(self, page: Page, url: str) -> bool:
        try:
            response = await page.goto(url, wait_until="domcontentloaded")
            await self._random_delay()
            if response and response.status >= 400:
                logger.warning("HTTP %s for %s", response.status, url)
                return False
            return True
        except Exception as exc:
            logger.error("Navigation error for %s: %s", url, exc)
            return False

    @staticmethod
    async def _random_delay() -> None:
        await asyncio.sleep(random.uniform(SCRAPER_DELAY_MIN, SCRAPER_DELAY_MAX))

    @staticmethod
    def _parse_price(raw: str | None) -> float | None:
        if not raw:
            return None
        cleaned = "".join(c for c in raw if c.isdigit() or c == ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _parse_int(raw: str | None) -> int | None:
        if not raw:
            return None
        cleaned = "".join(c for c in raw if c.isdigit())
        try:
            return int(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _parse_float(raw: str | None) -> float | None:
        if not raw:
            return None
        cleaned = "".join(c for c in raw if c.isdigit() or c == ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    #  Abstract interface                                                  #
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def scrape(
        self, district: str, property_type: str, max_pages: int = 20
    ) -> AsyncIterator[RawListing]:
        """Yield RawListing objects for the given district and property type."""
        ...
