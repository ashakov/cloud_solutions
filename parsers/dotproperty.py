"""
DotProperty parser — https://www.dotproperty.co.th

Site uses Next.js — full listing data lives in <script id="__NEXT_DATA__">.
Primary strategy: parse __NEXT_DATA__ JSON (fast, reliable, no DOM selectors).
Fallback: a[href*="/property/"] link extraction from rendered HTML.

Confirmed URL pattern (2026-04):
  https://www.dotproperty.co.th/properties-for-sale/phuket
    ?location=bang-tao&property_type=Condominium&page=1
"""
import json
import logging
import re
from typing import AsyncIterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import DOTPROPERTY_BASE_URL, SCRAPER_MAX_PAGES
from parsers.base import BaseParser, RawListing

logger = logging.getLogger(__name__)


class DotPropertyParser(BaseParser):
    SOURCE = "dotproperty"

    _SEARCH_URL = (
        "https://www.dotproperty.co.th/properties-for-sale/phuket"
        "?location={district}&property_type={prop_type}&page={page}"
    )

    _PROP_TYPE_MAP = {
        "condo":     "Condominium",
        "villa":     "Villa",
        "house":     "House",
        "townhouse": "Townhouse",
        "land":      "Land",
    }

    _FREEHOLD_KW  = {"freehold", "chanote", "โฉนด"}
    _LEASEHOLD_KW = {"leasehold", "lease", "สัญญาเช่า"}

    async def scrape(
        self, district: str, property_type: str = "condo", max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawListing]:
        prop_slug = self._PROP_TYPE_MAP.get(property_type, "Condominium")

        async with self:
            for page_num in range(1, max_pages + 1):
                url = self._SEARCH_URL.format(
                    district=district, prop_type=prop_slug, page=page_num
                )
                logger.info("[DotProperty] page %d — %s/%s", page_num, district, property_type)

                page = await self._new_page()
                ok = await self._goto(page, url)
                if not ok:
                    await page.close()
                    break

                # Scroll to trigger lazy-load
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2_500)

                html = await page.content()
                await page.close()

                soup = BeautifulSoup(html, "lxml")
                listings = list(self._parse_next_data(soup, district, property_type))

                if not listings:
                    # Fallback: extract from <a href="/property/..."> links
                    listings = list(self._parse_links(soup, district, property_type))

                logger.info("[DotProperty] %d listings on page %d", len(listings), page_num)

                if not listings:
                    break

                for item in listings:
                    yield item

                if len(listings) < 5:
                    break

    # ── Primary: __NEXT_DATA__ JSON ───────────────────────────────────────────

    def _parse_next_data(
        self, soup: BeautifulSoup, district: str, property_type: str
    ):
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if not script:
            logger.debug("[DotProperty] No __NEXT_DATA__ found")
            return

        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[DotProperty] Failed to parse __NEXT_DATA__ JSON")
            return

        # Navigate the Next.js page props tree to find listings
        listings_raw = self._dig(data, [
            "props", "pageProps", "listings",
            "props", "pageProps", "data", "listings",
            "props", "pageProps", "searchResults",
            "props", "pageProps", "properties",
            "props", "pageProps", "results",
        ])

        if not listings_raw:
            logger.debug("[DotProperty] No listings array in __NEXT_DATA__")
            return

        logger.info("[DotProperty] __NEXT_DATA__ found %d raw items", len(listings_raw))
        for item in listings_raw:
            listing = self._map_next_item(item, district, property_type)
            if listing:
                yield listing

    @staticmethod
    def _dig(data: dict, paths: list) -> list | None:
        """Try multiple key-path sequences to find a list of listings."""
        for path in paths:
            keys = path.split(", ") if isinstance(path, str) else path
            node = data
            try:
                for key in keys:
                    node = node[key]
                if isinstance(node, list) and node:
                    return node
            except (KeyError, TypeError):
                continue
        # Deep search: find first list with >3 items containing dict with 'price'/'id'
        def _search(obj, depth=0):
            if depth > 6:
                return None
            if isinstance(obj, list) and len(obj) > 3:
                if all(isinstance(i, dict) for i in obj[:3]):
                    sample = obj[0]
                    if any(k in sample for k in ("price", "listingPrice", "askingPrice", "id", "slug")):
                        return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    result = _search(v, depth + 1)
                    if result:
                        return result
            return None
        return _search(data)

    def _map_next_item(self, item: dict, district: str, property_type: str) -> RawListing | None:
        try:
            # ID and URL
            slug = item.get("slug") or item.get("id") or str(item.get("listingId", ""))
            source_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(slug))[:250]
            if not source_id:
                return None

            path = item.get("url") or item.get("path") or f"/property/{slug}"
            url = path if path.startswith("http") else urljoin(DOTPROPERTY_BASE_URL, path)

            # Price
            price_raw = (
                item.get("price") or item.get("listingPrice") or
                item.get("askingPrice") or item.get("priceThb")
            )
            price_thb = float(price_raw) if price_raw else None

            # Area
            area_raw = item.get("floorSize") or item.get("areaSqm") or item.get("size")
            area_sqm = float(area_raw) if area_raw else None

            # Beds / baths
            bedrooms  = self._safe_int(item.get("bedrooms") or item.get("bedroom"))
            bathrooms = self._safe_int(item.get("bathrooms") or item.get("bathroom"))

            # Title
            title = (
                item.get("title") or item.get("name") or
                item.get("projectName") or item.get("project")
            )

            # Location
            loc = item.get("location") or item.get("area") or item.get("district") or {}
            if isinstance(loc, dict):
                subdistrict = loc.get("name") or loc.get("area") or ""
            else:
                subdistrict = str(loc) if loc else ""

            # Ownership
            own_raw = str(item.get("ownershipType") or item.get("ownership") or "").lower()
            if "leasehold" in own_raw or "lease" in own_raw:
                ownership = "leasehold"
            elif "freehold" in own_raw or "chanote" in own_raw:
                ownership = "freehold"
            else:
                ownership = "unknown"

            # Features
            features = item.get("features") or item.get("amenities") or []
            feature_text = " ".join(str(f).lower() for f in features)
            has_pool   = bool(re.search(r"pool", feature_text))
            has_gym    = bool(re.search(r"gym|fitness", feature_text))
            has_garden = bool(re.search(r"garden", feature_text))

            # Rental / off-plan
            is_off_plan = bool(item.get("isOffPlan") or item.get("offPlan") or
                               re.search(r"off.?plan|pre.?sale", feature_text))
            rental_prog = bool(item.get("rentalProgram") or
                               re.search(r"rental program|guaranteed", feature_text))

            price_per_sqm = None
            if price_thb and area_sqm and area_sqm > 0:
                price_per_sqm = round(price_thb / area_sqm, 2)

            return RawListing(
                source=self.SOURCE,
                source_id=source_id,
                url=url,
                project_name=str(title) if title else None,
                district=district,
                subdistrict=subdistrict[:100] if subdistrict else None,
                property_type=property_type,
                ownership_type=ownership,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                area_sqm=area_sqm,
                price_thb=price_thb,
                price_per_sqm_thb=price_per_sqm,
                rental_program=rental_prog,
                has_pool=has_pool,
                has_gym=has_gym,
                has_garden=has_garden,
                is_off_plan=is_off_plan,
                raw_data={"source_json": {k: item[k] for k in list(item)[:20]}},
            )
        except Exception as exc:
            logger.exception("[DotProperty] item map error: %s", exc)
            return None

    # ── Fallback: link extraction ─────────────────────────────────────────────

    def _parse_links(self, soup: BeautifulSoup, district: str, property_type: str):
        seen: set[str] = set()
        for a in soup.select("a[href*='/property/']"):
            href = a.get("href", "")
            if not href or href in seen:
                continue
            seen.add(href)
            url = href if href.startswith("http") else urljoin(DOTPROPERTY_BASE_URL, href)
            source_id = re.sub(r"[^a-zA-Z0-9_-]", "_", href.strip("/").split("/")[-1])[:250]
            if not source_id:
                continue

            text = a.get_text(" ", strip=True)
            price_thb = self._parse_thb(text)
            yield RawListing(
                source=self.SOURCE,
                source_id=source_id,
                url=url,
                district=district,
                property_type=property_type,
                price_thb=price_thb,
                raw_data={"fallback": True, "link_text": text[:300]},
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_int(val) -> int | None:
        try:
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def _parse_thb(self, raw: str | None) -> float | None:
        if not raw:
            return None
        raw = raw.upper().replace(",", "").replace("THB", "").replace("฿", "").strip()
        mult = 1
        if "M" in raw:
            mult = 1_000_000
            raw = raw.replace("M", "")
        elif "K" in raw:
            mult = 1_000
            raw = raw.replace("K", "")
        cleaned = re.sub(r"[^\d.]", "", raw)
        try:
            return float(cleaned) * mult
        except ValueError:
            return None
