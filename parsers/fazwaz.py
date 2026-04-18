"""
FazWaz parser — https://www.fazwaz.com

Strategy:
  1. Load search-results page with Playwright (React/SSR hybrid).
  2. Intercept the internal JSON API response (/api/search or graphql) for
     structured data. If not available, fall back to HTML extraction via BS4.
  3. For each listing card click-through to the detail page to get CAM fees,
     sinking fund, and hotel-licence status.
"""
import json
import logging
import re
from typing import AsyncIterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import Page, Route, Request

from config import FAZWAZ_BASE_URL, SCRAPER_MAX_PAGES
from parsers.base import BaseParser, RawListing

logger = logging.getLogger(__name__)


class FazWazParser(BaseParser):
    SOURCE = "fazwaz"

    # Search URL for Phuket condos/villas per district
    _SEARCH_URL = (
        "https://www.fazwaz.com/property-for-sale/thailand/phuket/{district}"
        "?search%5Breal_estate_type_id%5D={type_id}&page={page}"
    )

    # FazWaz internal type IDs
    _TYPE_IDS = {
        "condo": "1",
        "villa": "2",
        "house": "3",
        "townhouse": "4",
        "land": "5",
    }

    # Ownership keywords
    _FREEHOLD_KEYWORDS = {"freehold", "โฉนด", "chanote"}
    _LEASEHOLD_KEYWORDS = {"leasehold", "สัญญาเช่า"}

    async def scrape(
        self, district: str, property_type: str = "condo", max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawListing]:
        type_id = self._TYPE_IDS.get(property_type, "1")

        async with self:
            for page_num in range(1, max_pages + 1):
                url = self._SEARCH_URL.format(
                    district=district,
                    type_id=type_id,
                    page=page_num,
                )
                logger.info("[FazWaz] Scraping page %d — %s/%s", page_num, district, property_type)

                page = await self._new_page()

                # Intercept JSON API calls to capture structured data
                captured_api: list[dict] = []

                async def handle_route(route: Route, request: Request) -> None:
                    await route.continue_()

                async def capture_response(response) -> None:
                    url_r = response.url
                    if (
                        "/api/" in url_r or "graphql" in url_r or "search" in url_r
                    ) and "fazwaz" in url_r:
                        try:
                            body = await response.json()
                            if isinstance(body, dict) and (
                                "data" in body or "listings" in body or "results" in body
                            ):
                                captured_api.append(body)
                        except Exception:
                            pass

                page.on("response", capture_response)

                ok = await self._goto(page, url)
                if not ok:
                    await page.close()
                    break

                # Wait for listing cards to appear
                try:
                    await page.wait_for_selector(
                        "[data-testid='listing-card'], .listing-card, article.property-card,"
                        " div[class*='PropertyCard'], a[href*='/property-for-sale/']",
                        timeout=15_000,
                    )
                except Exception:
                    logger.warning("[FazWaz] No listing cards on page %d", page_num)
                    await page.close()
                    break

                # Give React time to hydrate
                await page.wait_for_timeout(2_000)

                html = await page.content()
                soup = BeautifulSoup(html, "lxml")

                listings_found = 0
                async for listing in self._parse_search_page(soup, district, property_type, page):
                    listings_found += 1
                    yield listing

                await page.close()

                if listings_found == 0:
                    logger.info("[FazWaz] No more listings, stopping at page %d", page_num)
                    break

    async def _parse_search_page(
        self, soup: BeautifulSoup, district: str, property_type: str, playwright_page: Page
    ) -> AsyncIterator[RawListing]:
        # Try multiple card selectors — FazWaz has changed layouts over time
        cards = (
            soup.select("[data-testid='listing-card']")
            or soup.select("article.listing")
            or soup.select("div[class*='PropertyCard']")
            or soup.select("div[class*='ListingCard']")
            or soup.select(".search-result-item")
        )

        if not cards:
            # Last resort: find all links pointing to property detail pages
            cards = [
                a.parent
                for a in soup.select("a[href*='/property-for-sale/thailand/phuket/']")
                if a.parent
            ]
            # Deduplicate
            seen = set()
            unique_cards = []
            for c in cards:
                cid = id(c)
                if cid not in seen:
                    seen.add(cid)
                    unique_cards.append(c)
            cards = unique_cards

        logger.debug("[FazWaz] Found %d cards", len(cards))

        for card in cards:
            listing = self._extract_card(card, district, property_type)
            if listing:
                # Optionally enrich with detail page data
                # await self._enrich_from_detail(listing, playwright_page)
                yield listing

    def _extract_card(
        self, card: BeautifulSoup, district: str, property_type: str
    ) -> RawListing | None:
        try:
            # ---- URL & ID ------------------------------------------------
            link_tag = card.select_one("a[href*='/property-for-sale/']") or card.find("a")
            if not link_tag:
                return None
            href = link_tag.get("href", "")
            if not href:
                return None
            url = href if href.startswith("http") else urljoin(FAZWAZ_BASE_URL, href)
            source_id = re.sub(r"[^a-zA-Z0-9_-]", "_", href.strip("/").split("/")[-1])[:250]

            # ---- Price ---------------------------------------------------
            price_raw = self._text(card, [
                "[data-testid='listing-price']",
                ".price", "span[class*='price']", "div[class*='Price']",
                "strong[class*='price']",
            ])
            price_thb = self._parse_thb(price_raw)

            # ---- Area m² -------------------------------------------------
            area_raw = self._text(card, [
                "[data-testid='listing-size']",
                "span[class*='size']", "div[class*='Size']",
                ".area", "span[class*='area']",
            ])
            area_sqm = self._parse_sqm(area_raw)

            # ---- Bedrooms / Bathrooms ------------------------------------
            bed_raw = self._text(card, [
                "[data-testid='listing-bedroom']",
                "span[class*='bedroom']", "span[class*='bed']",
                "li[class*='bed']",
            ])
            bath_raw = self._text(card, [
                "[data-testid='listing-bathroom']",
                "span[class*='bathroom']", "span[class*='bath']",
                "li[class*='bath']",
            ])

            # ---- Project / Title -----------------------------------------
            title_raw = self._text(card, [
                "[data-testid='listing-title']", "h2", "h3",
                ".project-name", "span[class*='title']",
            ])

            # ---- Ownership type ------------------------------------------
            card_text = card.get_text(" ", strip=True).lower()
            ownership = self._detect_ownership(card_text)
            leasehold_years = self._detect_leasehold_years(card_text) if ownership == "leasehold" else None

            # ---- Price per m² -------------------------------------------
            price_per_sqm = None
            if price_thb and area_sqm and area_sqm > 0:
                price_per_sqm = price_thb / area_sqm

            listing = RawListing(
                source=self.SOURCE,
                source_id=source_id,
                url=url,
                project_name=title_raw,
                district=district,
                property_type=property_type,
                ownership_type=ownership,
                leasehold_years=leasehold_years,
                bedrooms=self._parse_int(bed_raw),
                bathrooms=self._parse_int(bath_raw),
                area_sqm=area_sqm,
                price_thb=price_thb,
                price_per_sqm_thb=price_per_sqm,
                raw_data={
                    "card_text": card_text[:500],
                    "price_raw": price_raw,
                    "area_raw": area_raw,
                    "bed_raw": bed_raw,
                    "url": url,
                },
            )
            return listing

        except Exception as exc:
            logger.exception("[FazWaz] Card extraction error: %s", exc)
            return None

    async def _enrich_from_detail(self, listing: RawListing, browser_page: Page) -> None:
        """Load detail page to extract CAM fee, sinking fund, hotel licence."""
        try:
            detail_page = await self._new_page()
            ok = await self._goto(detail_page, listing.url)
            if not ok:
                await detail_page.close()
                return

            html = await detail_page.content()
            soup = BeautifulSoup(html, "lxml")
            text = soup.get_text(" ", strip=True).lower()

            # CAM fee
            cam_match = re.search(r"(?:cam|common area)[^฿\d]*฿?\s*([\d,]+)\s*/\s*m", text)
            if cam_match:
                listing.cam_fee_per_sqm = self._parse_float(cam_match.group(1).replace(",", ""))

            # Sinking fund
            sf_match = re.search(r"sinking fund[^฿\d]*฿?\s*([\d,]+)\s*/\s*m", text)
            if sf_match:
                listing.sinking_fund_per_sqm = self._parse_float(sf_match.group(1).replace(",", ""))

            # Hotel licence
            listing.has_hotel_license = bool(
                re.search(r"hotel\s+licen[sc]e|hotel\s+permit", text)
            )

            # Developer name
            dev_tag = soup.select_one(
                ".developer-name, [class*='developer'], [data-testid='developer']"
            )
            if dev_tag:
                listing.developer = dev_tag.get_text(strip=True)

            await detail_page.close()
        except Exception as exc:
            logger.warning("[FazWaz] Detail page error for %s: %s", listing.url, exc)

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
        for sel in selectors:
            tag = soup.select_one(sel)
            if tag:
                t = tag.get_text(strip=True)
                if t:
                    return t
        return None

    def _parse_thb(self, raw: str | None) -> float | None:
        if not raw:
            return None
        raw = raw.upper().replace(",", "").replace("THB", "").replace("฿", "").strip()
        multiplier = 1
        if "M" in raw:
            multiplier = 1_000_000
            raw = raw.replace("M", "")
        elif "K" in raw:
            multiplier = 1_000
            raw = raw.replace("K", "")
        cleaned = re.sub(r"[^\d.]", "", raw)
        try:
            return float(cleaned) * multiplier
        except ValueError:
            return None

    @staticmethod
    def _parse_sqm(raw: str | None) -> float | None:
        if not raw:
            return None
        match = re.search(r"([\d,]+\.?\d*)\s*(?:sq\.?\s*m|m²|sqm)", raw, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
        cleaned = re.sub(r"[^\d.]", "", raw)
        try:
            val = float(cleaned)
            return val if val > 0 else None
        except ValueError:
            return None

    def _detect_ownership(self, text: str) -> str:
        if any(k in text for k in self._LEASEHOLD_KEYWORDS):
            return "leasehold"
        if any(k in text for k in self._FREEHOLD_KEYWORDS):
            return "freehold"
        return "unknown"

    @staticmethod
    def _detect_leasehold_years(text: str) -> int | None:
        match = re.search(r"(\d+)\s*[-–]?\s*year\s+lease", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"lease(?:hold)?\s+(\d+)\s*year", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
