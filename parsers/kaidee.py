"""
Kaidee Property — https://www.kaidee.com/property

Horizontal marketplace with direct-from-owner listings.
Search: https://www.kaidee.com/property?st=2&q=phuket&cid=920
  st=2 → for sale; cid=920 → property category
"""
import logging
import re
from typing import AsyncIterator

from config import SCRAPER_MAX_PAGES
from parsers.base import RawListing
from parsers.scrapling_base import ScraplingParser

logger = logging.getLogger(__name__)

_BASE = "https://www.kaidee.com"


class KaideeParser(ScraplingParser):
    SOURCE = "kaidee"

    # st=2 for sale, cid=920 property
    _SEARCH_URL = f"{_BASE}/property?st=2&q=phuket&cid=920&page={{page}}"

    async def scrape(
        self, district: str, property_type: str = "condo", max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawListing]:
        for page_num in range(1, max_pages + 1):
            url = self._SEARCH_URL.format(page=page_num)
            logger.info("[Kaidee] page %d", page_num)

            page = self._fetch(url)
            if page is None:
                break

            # Try __NEXT_DATA__ first
            data = self._extract_next_data(page)
            items = self._find_items(data)

            # Fallback: CSS cards
            if not items:
                items = self._scrape_cards(page)

            if not items:
                break

            count = 0
            for item in items:
                listing = self._map_item(item)
                if listing:
                    count += 1
                    yield listing

            logger.info("[Kaidee] %d listings on page %d", count, page_num)
            await self._polite_delay()

            if count == 0:
                break

    def _find_items(self, data: dict, depth: int = 0) -> list:
        if depth > 5:
            return []
        for key in ("ads", "listings", "items", "data", "props", "results"):
            if key in data:
                v = data[key]
                if isinstance(v, list) and v:
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
        try:
            cards = page.css("article, .listing-card, [data-testid='ad-card'], .ad-item")
            results = []
            for card in (cards or []):
                try:
                    text = card.get_text(" ", strip=True) if hasattr(card, "get_text") else str(card)
                    a = card.css_first("a[href]")
                    href = a.attrib.get("href", "") if a else ""
                    if href:
                        results.append({"_text": text, "_href": href})
                except Exception:
                    pass
            return results
        except Exception:
            return []

    def _map_item(self, raw: dict) -> RawListing | None:
        try:
            href = raw.get("_href") or raw.get("url") or raw.get("link") or ""
            text = raw.get("_text") or str(raw)
            t = text.lower()

            # Only keep Phuket-related listings
            if "phuket" not in t and "ภูเก็ต" not in t:
                return None

            url = href if href.startswith("http") else f"{_BASE}{href}"
            lid = re.sub(r"\W", "_", href.strip("/").split("/")[-1])[:250] if href else None
            if not lid:
                lid = str(raw.get("id") or raw.get("adId") or "")
            if not lid:
                return None

            price = self._parse_thb(
                str(raw.get("price") or raw.get("priceText") or text[:150])
            )

            # Extract beds/area from text
            beds_m = re.search(r"(\d+)\s*(?:bed|br|ห้องนอน)", t)
            area_m = re.search(r"([\d,]+)\s*(?:sqm|sq\.m|ตร\.ม|m²)", t)
            beds = int(beds_m.group(1)) if beds_m else None
            area_sqm = float(area_m.group(1).replace(",", "")) if area_m else None

            pt_raw = str(raw.get("propertyType") or raw.get("category") or "").lower()
            if "villa" in pt_raw or "villa" in t:
                prop_type = "villa"
            elif "land" in pt_raw or "ที่ดิน" in t:
                prop_type = "land"
            elif "town" in pt_raw:
                prop_type = "townhouse"
            elif "house" in pt_raw or "บ้าน" in t:
                prop_type = "house"
            else:
                prop_type = "condo"

            dist = "phuket-other"
            for slug, kws in [
                ("bang-tao", ["bang tao", "laguna"]), ("kamala", ["kamala"]),
                ("patong", ["patong"]), ("rawai", ["rawai"]),
                ("cherng-talay", ["cherng talay", "choeng thale"]),
                ("chalong", ["chalong"]), ("phuket-town", ["phuket town"]),
                ("surin", ["surin"]), ("kata", ["kata"]), ("karon", ["karon"]),
            ]:
                if any(kw in t for kw in kws):
                    dist = slug
                    break

            own = "unknown"
            if "freehold" in t:
                own = "freehold"
            elif "lease" in t:
                own = "leasehold"

            price_sqm = round(price / area_sqm, 2) if price and area_sqm else None

            return RawListing(
                source=self.SOURCE,
                source_id=lid,
                url=url,
                district=dist,
                property_type=prop_type,
                ownership_type=own,
                bedrooms=beds,
                area_sqm=area_sqm,
                price_thb=price,
                price_per_sqm_thb=price_sqm,
                has_pool=bool(re.search(r"\bpool\b|\bสระ", t)),
                is_off_plan=bool(re.search(r"off.?plan|pre.?sale|under construction", t)),
            )
        except Exception as exc:
            logger.debug("[Kaidee] map error: %s", exc)
            return None
