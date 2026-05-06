"""
DDproperty (PropertyGuru Thailand) — https://www.ddproperty.com

Site: Next.js SSR → full listing data in <script id="__NEXT_DATA__">.
Strategy: plain HTTP via Scrapling (no browser needed), extract __NEXT_DATA__.

Search URL pattern:
  https://www.ddproperty.com/en/property-for-sale?
    freetext=Phuket&listing_type=sale&property_type_code[]=CONDO&page_num=1
"""
import logging
import re
from typing import AsyncIterator

from config import SCRAPER_MAX_PAGES
from parsers.base import RawListing
from parsers.scrapling_base import ScraplingParser

logger = logging.getLogger(__name__)

_BASE = "https://www.ddproperty.com"


class DDPropertyParser(ScraplingParser):
    SOURCE = "ddproperty"

    _TYPE_CODES = {
        "condo":     "CONDO",
        "villa":     "BUNG",       # bungalow / villa
        "house":     "TERR",       # terrace / house
        "townhouse": "TOWN",
        "land":      "LAND",
    }

    _DISTRICT_KW = {
        "bang-tao": ["bang tao", "bangtao", "laguna"],
        "kamala":   ["kamala"],
        "patong":   ["patong"],
        "rawai":    ["rawai"],
        "kata":     ["kata"],
        "karon":    ["karon"],
        "surin":    ["surin"],
        "nai-harn": ["nai harn", "naiharn"],
        "cherng-talay": ["cherng talay", "choeng thale"],
        "chalong":  ["chalong"],
        "phuket-town": ["phuket town"],
        "mai-khao": ["mai khao", "maikhao"],
        "layan":    ["layan"],
    }

    def _build_url(self, prop_type: str, page: int) -> str:
        code = self._TYPE_CODES.get(prop_type, "CONDO")
        return (
            f"{_BASE}/en/property-for-sale"
            f"?freetext=Phuket&listing_type=sale"
            f"&property_type_code[]={code}&page_num={page}"
        )

    def _to_district(self, text: str) -> str:
        t = (text or "").lower()
        for slug, kws in self._DISTRICT_KW.items():
            if any(kw in t for kw in kws):
                return slug
        if "phuket" in t:
            return "phuket-other"
        return "phuket-other"

    async def scrape(
        self, district: str, property_type: str = "condo", max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawListing]:
        for page_num in range(1, max_pages + 1):
            url = self._build_url(property_type, page_num)
            logger.info("[DDProperty] page %d — %s", page_num, property_type)

            page = self._fetch(url)
            if page is None:
                break

            data = self._extract_next_data(page)
            listings_raw = self._dig_listings(data)

            if not listings_raw:
                logger.info("[DDProperty] no listings on page %d, stopping", page_num)
                break

            count = 0
            for raw in listings_raw:
                listing = self._map_listing(raw)
                if listing:
                    count += 1
                    yield listing

            logger.info("[DDProperty] %d listings extracted on page %d", count, page_num)
            await self._polite_delay()

            if count == 0:
                break

    def _dig_listings(self, data: dict) -> list:
        """Recursively find the listings array in __NEXT_DATA__."""
        for key in ("searchResult", "listingsByKeyword", "listings", "data", "results"):
            if key in data:
                v = data[key]
                if isinstance(v, list) and v:
                    return v
                if isinstance(v, dict):
                    inner = self._dig_listings(v)
                    if inner:
                        return inner
        # Walk all dict values
        for v in data.values():
            if isinstance(v, dict):
                inner = self._dig_listings(v)
                if inner:
                    return inner
        return []

    def _map_listing(self, raw: dict) -> RawListing | None:
        try:
            url = raw.get("url") or raw.get("listingUrl") or ""
            if not url:
                lid = raw.get("id") or raw.get("listingId") or ""
                if not lid:
                    return None
                url = f"{_BASE}/en/property/{lid}"

            source_id = str(raw.get("id") or raw.get("listingId") or re.sub(r"\W", "_", url[-60:]))
            price_raw = (raw.get("price") or raw.get("priceFormatted") or
                         str(raw.get("priceRaw") or ""))
            price = self._parse_thb(str(price_raw)) if price_raw else None

            location = (raw.get("location") or raw.get("district") or
                        raw.get("address") or "")
            if isinstance(location, dict):
                location = " ".join(str(v) for v in location.values() if v)

            attrs = raw.get("attributes") or raw.get("specs") or {}
            beds = (raw.get("bedroomCount") or raw.get("bedrooms") or
                    attrs.get("bedrooms") or attrs.get("bedroomCount"))
            baths = (raw.get("bathroomCount") or raw.get("bathrooms") or
                     attrs.get("bathrooms"))
            area = (raw.get("area") or raw.get("floorArea") or
                    attrs.get("floorArea") or attrs.get("area"))

            pt = (raw.get("propertyType") or raw.get("property_type") or "").lower()
            if "villa" in pt or "bung" in pt:
                prop_type = "villa"
            elif "land" in pt:
                prop_type = "land"
            elif "town" in pt:
                prop_type = "townhouse"
            elif "house" in pt or "terr" in pt:
                prop_type = "house"
            else:
                prop_type = "condo"

            title = raw.get("title") or raw.get("name") or ""
            card_text = str(raw).lower()
            if "freehold" in card_text:
                ownership = "freehold"
            elif "leasehold" in card_text or "lease" in card_text:
                ownership = "leasehold"
            else:
                ownership = "unknown"

            area_sqm = float(str(area).replace(",", "").replace("sqm", "").strip()) if area else None
            price_sqm = round(price / area_sqm, 2) if price and area_sqm and area_sqm > 0 else None

            return RawListing(
                source=self.SOURCE,
                source_id=source_id[:300],
                url=url if url.startswith("http") else f"{_BASE}{url}",
                project_name=title or None,
                district=self._to_district(location),
                property_type=prop_type,
                ownership_type=ownership,
                bedrooms=int(beds) if beds is not None else None,
                bathrooms=int(baths) if baths is not None else None,
                area_sqm=area_sqm,
                price_thb=price,
                price_per_sqm_thb=price_sqm,
                has_pool=bool(re.search(r"\bpool\b", card_text)),
                has_gym=bool(re.search(r"\bgym\b|\bfitness\b", card_text)),
                is_off_plan=bool(re.search(r"off.?plan|pre.?sale|pre-?launch", card_text)),
            )
        except Exception as exc:
            logger.debug("[DDProperty] map error: %s", exc)
            return None
