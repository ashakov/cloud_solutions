"""
DotProperty parser — https://www.dotproperty.co.th

Strategy:
  1. Use Playwright to load search pages (SSR + lazy-load hydration).
  2. Extract listing cards via CSS selectors with multiple fallbacks.
  3. Scroll the page to trigger lazy loading before extracting.
"""
import logging
import re
from typing import AsyncIterator
from urllib.parse import urljoin, urlencode

from bs4 import BeautifulSoup

from config import DOTPROPERTY_BASE_URL, SCRAPER_MAX_PAGES
from parsers.base import BaseParser, RawListing

logger = logging.getLogger(__name__)


class DotPropertyParser(BaseParser):
    SOURCE = "dotproperty"

    # Working URL pattern confirmed via probe: ?location= filter works
    _SEARCH_URL = (
        "https://www.dotproperty.co.th/properties-for-sale/phuket"
        "?location={district}&property_type={prop_type}&page={page}"
    )

    # DotProperty property type slugs
    _PROP_TYPE_MAP = {
        "condo":      "Condominium",
        "villa":      "Villa",
        "house":      "House",
        "townhouse":  "Townhouse",
        "land":       "Land",
    }

    _FREEHOLD_KW = {"freehold", "chanote", "โฉนด"}
    _LEASEHOLD_KW = {"leasehold", "lease", "สัญญาเช่า"}

    async def scrape(
        self, district: str, property_type: str = "condo", max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawListing]:
        prop_slug = self._PROP_TYPE_MAP.get(property_type, "Condominium")

        async with self:
            for page_num in range(1, max_pages + 1):
                url = self._SEARCH_URL.format(
                    district=district,
                    prop_type=prop_slug,
                    page=page_num,
                )
                logger.info(
                    "[DotProperty] Scraping page %d — %s/%s", page_num, district, property_type
                )

                page = await self._new_page()
                ok = await self._goto(page, url)
                if not ok:
                    await page.close()
                    break

                # Scroll to bottom to trigger lazy-loaded cards
                await page.evaluate(
                    "window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})"
                )
                await page.wait_for_timeout(2_000)

                try:
                    await page.wait_for_selector(
                        ".listing-card, article[class*='listing'], "
                        "div[class*='PropertyCard'], .property-item, "
                        "li[class*='listing'], a[href*='/property/']",
                        timeout=15_000,
                    )
                except Exception:
                    logger.warning("[DotProperty] No cards on page %d", page_num)
                    await page.close()
                    break

                html = await page.content()
                await page.close()

                soup = BeautifulSoup(html, "lxml")
                listings_found = 0

                for listing in self._parse_page(soup, district, property_type):
                    listings_found += 1
                    yield listing

                if listings_found == 0:
                    logger.info("[DotProperty] No listings at page %d, stopping", page_num)
                    break

    def _parse_page(
        self, soup: BeautifulSoup, district: str, property_type: str
    ):
        cards = (
            soup.select("article[class*='listing']")
            or soup.select("div[class*='PropertyCard']")
            or soup.select(".property-item")
            or soup.select("li[class*='listing']")
            or soup.select(".listing-card")
        )

        if not cards:
            # Fallback: any <a> pointing to /property/ pages
            seen_hrefs: set[str] = set()
            for a in soup.select("a[href*='/property/']"):
                href = a.get("href", "")
                if href and href not in seen_hrefs:
                    seen_hrefs.add(href)
                    listing = self._minimal_listing(a, href, district, property_type)
                    if listing:
                        yield listing
            return

        logger.debug("[DotProperty] Found %d cards", len(cards))
        for card in cards:
            result = self._extract_card(card, district, property_type)
            if result:
                yield result

    def _extract_card(
        self, card: BeautifulSoup, district: str, property_type: str
    ) -> RawListing | None:
        try:
            # URL
            link = card.select_one("a[href*='/property/']") or card.find("a")
            if not link:
                return None
            href = link.get("href", "")
            if not href:
                return None
            url = href if href.startswith("http") else urljoin(DOTPROPERTY_BASE_URL, href)
            source_id = re.sub(r"[^a-zA-Z0-9_-]", "_", href.strip("/").split("/")[-1])[:250]

            # Price — DotProperty typically shows "฿ 3,500,000" or "THB 3.5M"
            price_raw = self._text(card, [
                "[class*='price']", "[class*='Price']",
                "span[class*='amount']", ".asking-price",
                "strong[class*='price']", "p[class*='price']",
            ])
            price_thb = self._parse_thb(price_raw)

            # Area
            area_raw = self._text(card, [
                "[class*='size']", "[class*='area']", "[class*='sqm']",
                "span[class*='floor']",
            ])
            area_sqm = self._parse_sqm(area_raw)

            # Beds / baths
            bed_raw = self._text(card, [
                "[class*='bedroom']", "[class*='bed']",
                "li[class*='bed']", "span[data-testid*='bed']",
            ])
            bath_raw = self._text(card, [
                "[class*='bathroom']", "[class*='bath']",
                "li[class*='bath']",
            ])

            # Title / project
            title_raw = self._text(card, [
                "h2", "h3", "h4",
                "[class*='title']", "[class*='name']",
            ])

            card_text = card.get_text(" ", strip=True).lower()
            ownership = self._detect_ownership(card_text)
            leasehold_years = (
                self._detect_leasehold_years(card_text) if ownership == "leasehold" else None
            )

            price_per_sqm = None
            if price_thb and area_sqm and area_sqm > 0:
                price_per_sqm = price_thb / area_sqm

            has_pool   = bool(re.search(r"\bpool\b|\bสระ", card_text))
            has_garden = bool(re.search(r"\bgarden\b|\bสวน", card_text))
            has_gym    = bool(re.search(r"\bgym\b|\bfitness", card_text))
            is_off_plan = bool(re.search(r"off.?plan|pre.?sale|under construction", card_text))
            rental_prog = bool(re.search(r"rental program|rental guarantee|guaranteed", card_text))
            has_hl = bool(re.search(r"hotel licen[sc]e|hotel permit", card_text))

            if re.search(r"short.?term|daily|weekly|holiday rental", card_text):
                r_type = "short_term"
            elif re.search(r"long.?term|annual|monthly rent", card_text):
                r_type = "long_term"
            else:
                r_type = "unknown"

            monthly_rent = None
            rent_match = re.search(
                r"(?:rent|rental)[^\d฿]*฿?\s*([\d,]+)\s*/\s*(?:month|mo\b)", card_text
            )
            if rent_match:
                monthly_rent = self._parse_float(rent_match.group(1).replace(",", ""))

            return RawListing(
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
                monthly_rent_thb=monthly_rent,
                rental_type=r_type,
                rental_program=rental_prog,
                has_hotel_license=has_hl,
                has_pool=has_pool,
                has_garden=has_garden,
                has_gym=has_gym,
                is_off_plan=is_off_plan,
                raw_data={
                    "url": url,
                    "price_raw": price_raw,
                    "area_raw": area_raw,
                    "card_text": card_text[:500],
                },
            )
        except Exception as exc:
            logger.exception("[DotProperty] Card error: %s", exc)
            return None

    def _minimal_listing(
        self, tag: BeautifulSoup, href: str, district: str, property_type: str
    ) -> RawListing | None:
        url = href if href.startswith("http") else urljoin(DOTPROPERTY_BASE_URL, href)
        source_id = re.sub(r"[^a-zA-Z0-9_-]", "_", href.strip("/").split("/")[-1])[:250]
        text = tag.get_text(" ", strip=True)
        price_thb = self._parse_thb(text)
        return RawListing(
            source=self.SOURCE,
            source_id=source_id,
            url=url,
            district=district,
            property_type=property_type,
            price_thb=price_thb,
            raw_data={"url": url, "link_text": text[:300]},
        )

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
            return val if 0 < val < 10_000 else None
        except ValueError:
            return None

    def _detect_ownership(self, text: str) -> str:
        if any(k in text for k in self._LEASEHOLD_KW):
            return "leasehold"
        if any(k in text for k in self._FREEHOLD_KW):
            return "freehold"
        return "unknown"

    @staticmethod
    def _detect_leasehold_years(text: str) -> int | None:
        match = re.search(r"(\d+)\s*[-–]?\s*year\s+lease", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
