"""
Hipflat — https://www.hipflat.co.th

Aggregates listings from multiple Thai portals. Next.js SSR.
Search: https://www.hipflat.co.th/en/search?q=Phuket&type=sale&category=condo&page=1
"""
import logging
import re
from typing import AsyncIterator

from config import SCRAPER_MAX_PAGES
from parsers.base import RawListing
from parsers.scrapling_base import ScraplingParser

logger = logging.getLogger(__name__)

_BASE = "https://www.hipflat.co.th"


class HipflatParser(ScraplingParser):
    SOURCE = "hipflat"

    _CATEGORY_MAP = {
        "condo":     "condo",
        "villa":     "villa",
        "house":     "house",
        "townhouse": "townhouse",
        "land":      "land",
    }

    def _build_url(self, prop_type: str, page: int) -> str:
        cat = self._CATEGORY_MAP.get(prop_type, "condo")
        return (
            f"{_BASE}/en/search?q=Phuket&type=sale&category={cat}&page={page}"
        )

    async def scrape(
        self, district: str, property_type: str = "condo", max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawListing]:
        for page_num in range(1, max_pages + 1):
            url = self._build_url(property_type, page_num)
            logger.info("[Hipflat] page %d — %s", page_num, property_type)

            page = self._fetch(url)
            if page is None:
                break

            data = self._extract_next_data(page)
            items = self._find_items(data)

            if not items:
                # Fallback: CSS selector on rendered HTML
                items = self._scrape_cards(page)

            if not items:
                logger.info("[Hipflat] no items on page %d, stopping", page_num)
                break

            count = 0
            for item in items:
                listing = self._map_item(item, property_type)
                if listing:
                    count += 1
                    yield listing

            logger.info("[Hipflat] %d listings on page %d", count, page_num)
            await self._polite_delay()

            if count == 0:
                break

    def _find_items(self, data: dict, depth: int = 0) -> list:
        if depth > 6:
            return []
        for key in ("listings", "properties", "results", "items", "data", "props"):
            if key in data:
                v = data[key]
                if isinstance(v, list) and len(v) > 0:
                    return v
                if isinstance(v, dict):
                    inner = self._find_items(v, depth + 1)
                    if inner:
                        return inner
        for v in data.values():
            if isinstance(v, dict):
                inner = self._find_items(v, depth + 1)
                if inner:
                    return inner
        return []

    def _scrape_cards(self, page) -> list:
        """Fallback: collect listing cards from rendered HTML."""
        try:
            cards = page.css(".listing-card, .property-card, article[data-id]")
            results = []
            for card in cards:
                text = card.get_text(" ", strip=True) if hasattr(card, "get_text") else str(card)
                href = ""
                try:
                    a = card.css_first("a[href]")
                    href = a.attrib.get("href", "") if a else ""
                except Exception:
                    pass
                results.append({"_card_text": text, "_href": href})
            return results
        except Exception:
            return []

    def _map_item(self, raw: dict, property_type: str) -> RawListing | None:
        try:
            card_text = raw.get("_card_text", "")
            href = raw.get("_href", "")

            # JSON item
            url = raw.get("url") or raw.get("link") or href or ""
            lid = raw.get("id") or raw.get("listingId") or re.sub(r"\W", "_", url[-50:])
            if not lid:
                return None

            url = url if url.startswith("http") else f"{_BASE}{url}"

            price = self._parse_thb(
                str(raw.get("price") or raw.get("priceFormatted") or card_text[:100])
            )
            location = str(raw.get("location") or raw.get("district") or raw.get("address") or "")
            beds = raw.get("bedrooms") or raw.get("bedroomCount")
            baths = raw.get("bathrooms") or raw.get("bathroomCount")
            area = raw.get("area") or raw.get("floorArea")

            t = card_text.lower() + str(raw).lower()
            if "freehold" in t:
                own = "freehold"
            elif "lease" in t:
                own = "leasehold"
            else:
                own = "unknown"

            area_sqm = None
            if area:
                try:
                    area_sqm = float(str(area).replace(",", "").split()[0])
                except Exception:
                    pass
            if not area_sqm:
                m = re.search(r"([\d,]+)\s*(?:sqm|sq\.m|m²)", t)
                if m:
                    area_sqm = float(m.group(1).replace(",", ""))

            dist = "phuket-other"
            for slug, kws in [
                ("bang-tao", ["bang tao", "laguna"]), ("kamala", ["kamala"]),
                ("patong", ["patong"]), ("rawai", ["rawai"]), ("surin", ["surin"]),
                ("cherng-talay", ["cherng talay", "choeng thale"]),
                ("chalong", ["chalong"]), ("phuket-town", ["phuket town"]),
            ]:
                if any(kw in location.lower() or kw in t for kw in kws):
                    dist = slug
                    break

            price_sqm = round(price / area_sqm, 2) if price and area_sqm and area_sqm > 0 else None

            return RawListing(
                source=self.SOURCE,
                source_id=str(lid)[:300],
                url=url,
                district=dist,
                property_type=property_type,
                ownership_type=own,
                bedrooms=int(beds) if beds is not None else None,
                bathrooms=int(baths) if baths is not None else None,
                area_sqm=area_sqm,
                price_thb=price,
                price_per_sqm_thb=price_sqm,
                has_pool=bool(re.search(r"\bpool\b", t)),
                is_off_plan=bool(re.search(r"off.?plan|pre.?sale", t)),
            )
        except Exception as exc:
            logger.debug("[Hipflat] map error: %s", exc)
            return None
