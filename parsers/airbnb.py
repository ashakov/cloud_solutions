"""
Airbnb parser — https://www.airbnb.com/s/Phuket--Thailand/homes

Data source (confirmed 2026-04):
  Airbnb embeds ALL search-result data server-side in a single <script> tag
  under the key "niobeClientData".  No API interception needed.

  Card node signature:  has "structuredDisplayPrice" + ("title" or "subtitle")
  Card fields used:
    demandStayListing.id   → base64 decode → numeric listing ID
    title                  → "Tiny home in Choeng Thale" → location + room type
    subtitle               → actual unit name ("Grand Utopia Pool Villa")
    avgRatingLocalized     → "4.92 (12)"  or "New"
    structuredDisplayPrice.primaryLine.accessibilityLabel
                           → "฿14,742 for 7 nights"  → /7 = nightly rate
    structuredContent.primaryLine[type=BEDINFO].body → "2 bedrooms"

Seasonal pricing:
  We run 4 passes (peak/high/shoulder/low) with representative check-in dates.
  Each pass yields listings tagged with that season; the upsert layer merges
  the 4 seasonal rates onto the same rental_listing row.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from typing import AsyncIterator, Any

from parsers.base import BaseParser
from parsers.fazwaz_rental import RawRentalListing

logger = logging.getLogger(__name__)

# ── Season windows ────────────────────────────────────────────────────────────
_SEASON_DATES: dict[str, tuple[str, str]] = {
    "peak":     ("2026-12-24", "2026-12-31"),   # 7-night Christmas stay
    "high":     ("2027-02-10", "2027-02-17"),
    "shoulder": ("2027-05-05", "2027-05-12"),
    "low":      ("2027-07-08", "2027-07-15"),
}
_STAY_NIGHTS = 7  # all windows are 7-night → divide total price by 7

# ── Location mapping (same as FazWaz rental) ──────────────────────────────────
_LOCATION_MAP: dict[str, str] = {
    "bang tao":       "bang-tao",
    "bangtao":        "bang-tao",
    "laguna":         "bang-tao",
    "layan":          "layan",
    "surin":          "surin",
    "kamala":         "kamala",
    "patong":         "patong",
    "kata":           "kata",
    "karon":          "karon",
    "rawai":          "rawai",
    "nai harn":       "nai-harn",
    "naiharn":        "nai-harn",
    "choeng thale":   "cherng-talay",
    "cherng talay":   "cherng-talay",
    "si sunthon":     "layan",
    "mai khao":       "mai-khao",
    "sakhu":          "mai-khao",
    "chalong":        "chalong",
    "phuket town":    "phuket-town",
    "thalang":        "thalang",
    "kathu":          "kathu",
    "pa khlok":       "pa-khlok",
}

_ROOM_TYPE_MAP: dict[str, str] = {
    "villa":      "villa",
    "pool villa": "villa",
    "bungalow":   "villa",
    "home":       "villa",      # "Home in X" on Airbnb is usually a house/villa
    "house":      "house",
    "townhouse":  "house",
    "apartment":  "condo",
    "condo":      "condo",
    "studio":     "condo",
    "tiny home":  "condo",
    "guesthouse": "condo",
    "cottage":    "house",
}


def _location_to_district(text: str) -> str:
    t = (text or "").lower()
    for key, slug in _LOCATION_MAP.items():
        if key in t:
            return slug
    return "phuket-other"


def _decode_listing_id(encoded_id: str) -> str:
    """Base64 decode Airbnb's DemandStayListing ID → numeric string."""
    try:
        decoded = base64.b64decode(encoded_id + "==").decode("utf-8", errors="ignore")
        # Format: "DemandStayListing:1234567890"
        if ":" in decoded:
            return decoded.split(":")[-1].strip()
    except Exception:
        pass
    return encoded_id  # fall back to raw value


def _parse_price_label(label: str) -> float | None:
    """
    Parse Airbnb's accessibility price label.
    Examples:
      "฿14,742 for 7 nights"           -> 14742 / 7 = 2106
      "฿27,424 for 7 nights, originally ฿29,983"  -> 27424 / 7
      "THB 3,500 per night"            -> 3500
    """
    if not label:
        return None
    # Remove Thai baht symbol and commas
    label_clean = label.replace("฿", "").replace(",", "").strip()

    # "X for N nights" pattern
    m = re.search(r"([\d.]+)\s+for\s+(\d+)\s+night", label_clean, re.IGNORECASE)
    if m:
        total = float(m.group(1))
        nights = int(m.group(2))
        return round(total / nights, 2) if nights > 0 else None

    # "X per night" / "X / night" pattern
    m = re.search(r"([\d.]+)\s+(?:per\s+)?night", label_clean, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # Plain number fallback (only accept plausible nightly rates 500–500k THB)
    m = re.search(r"([\d.]+)", label_clean)
    if m:
        val = float(m.group(1))
        if 500 <= val <= 500_000:
            return val
    return None


def _parse_rating(raw: str | None) -> tuple[float | None, int | None]:
    """Parse "4.92 (12)" → (4.92, 12).  "New" → (None, None)."""
    if not raw or raw.strip().lower() in ("new", ""):
        return None, None
    m = re.match(r"([\d.]+)\s*\((\d+)\)", raw.strip())
    if m:
        return float(m.group(1)), int(m.group(2))
    m = re.match(r"([\d.]+)", raw.strip())
    if m:
        return float(m.group(1)), None
    return None, None


def _parse_bedrooms(structured_content: dict | None) -> int | None:
    """Extract bedroom count from structuredContent.primaryLine list."""
    if not structured_content:
        return None
    for section in ("primaryLine", "secondaryLine"):
        items = structured_content.get(section) or []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "BEDINFO":
                body = item.get("body", "")
                m = re.search(r"(\d+)\s*bedroom", body, re.IGNORECASE)
                if m:
                    return int(m.group(1))
                if "studio" in body.lower():
                    return 0
    return None


def _infer_type_from_title(title: str) -> str:
    """Infer property type from Airbnb's card title (e.g. 'Tiny home in X').
    Longer keys are checked first so 'tiny home' wins over 'home'."""
    t = title.lower()
    for kw, ptype in sorted(_ROOM_TYPE_MAP.items(), key=lambda x: -len(x[0])):
        if kw in t:
            return ptype
    return "condo"


def _find_card_nodes(obj: Any, results: list | None = None, depth: int = 0) -> list[dict]:
    """Recursively find card nodes: dicts with structuredDisplayPrice + title."""
    if results is None:
        results = []
    if depth > 15:
        return results
    if isinstance(obj, dict):
        if "structuredDisplayPrice" in obj and ("title" in obj or "subtitle" in obj):
            results.append(obj)
            return results  # don't recurse into found card
        for v in obj.values():
            _find_card_nodes(v, results, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _find_card_nodes(item, results, depth + 1)
    return results


def _parse_card(card: dict, season_tag: str) -> RawRentalListing | None:
    """Convert one niobeClientData card node → RawRentalListing."""
    # ── ID ────────────────────────────────────────────────────────────────────
    dsl = card.get("demandStayListing") or {}
    raw_id = dsl.get("id") or card.get("propertyId") or ""
    listing_id = _decode_listing_id(str(raw_id)) if raw_id else ""
    if not listing_id:
        return None

    url = f"https://www.airbnb.com/rooms/{listing_id}"

    # ── Title / name ──────────────────────────────────────────────────────────
    # title  = "Tiny home in Choeng Thale"   (type + location)
    # subtitle = actual property name
    title = card.get("title") or ""
    name_obj = card.get("nameLocalized") or {}
    name = (name_obj.get("localizedStringWithTranslationPreference")
            or card.get("subtitle") or title)

    # ── Location from title "TYPE in LOCATION" ────────────────────────────────
    location_text = ""
    m = re.search(r"\bin\s+(.+)$", title, re.IGNORECASE)
    if m:
        location_text = m.group(1).strip()
    district = _location_to_district(location_text or title)

    # ── Property type from title prefix ───────────────────────────────────────
    property_type = _infer_type_from_title(title)

    # ── Price ─────────────────────────────────────────────────────────────────
    sdp = card.get("structuredDisplayPrice") or {}
    primary = sdp.get("primaryLine") or {}
    price_label = (primary.get("accessibilityLabel")
                   or primary.get("discountedPrice")
                   or primary.get("price") or "")
    daily_rate = _parse_price_label(price_label)
    if not daily_rate:
        return None

    # ── Bedrooms ─────────────────────────────────────────────────────────────
    bedrooms = _parse_bedrooms(card.get("structuredContent"))

    # ── Rating / reviews ──────────────────────────────────────────────────────
    rating, reviews = _parse_rating(card.get("avgRatingLocalized"))

    # ── Amenities from name keywords ──────────────────────────────────────────
    name_lower = name.lower()
    has_pool    = bool(re.search(r"\bpool\b", name_lower))
    has_gym     = bool(re.search(r"\bgym\b|\bfitness", name_lower))
    has_sea_view = bool(re.search(r"sea\s*view|ocean\s*view|seaview", name_lower))

    return RawRentalListing(
        source="airbnb",
        source_id=listing_id,
        url=url,
        project_name=name[:400],
        district=district,
        property_type=property_type,
        bedrooms=bedrooms,
        bathrooms=None,          # not exposed in search cards
        area_sqm=None,           # not exposed in search cards
        has_pool=has_pool,
        has_gym=has_gym,
        has_sea_view=has_sea_view,
        price_thb=daily_rate,
        price_period="nightly",
        daily_rate_thb=daily_rate,
        season_tag=season_tag,
        rental_type="short_term",
        platform_rating=rating,
        platform_reviews_count=reviews,
        raw_data={
            "listing_id": listing_id,
            "title": title,
            "name": name,
            "location": location_text,
            "season": season_tag,
            "price_label": price_label,
            "avg_rating_raw": card.get("avgRatingLocalized"),
        },
        scraped_at=datetime.utcnow(),
    )


class AirbnbParser(BaseParser):
    SOURCE = "airbnb"

    _SEARCH_BASE = "https://www.airbnb.com/s/Phuket--Thailand/homes"

    def _build_search_url(self, checkin: str, checkout: str) -> str:
        return (
            f"{self._SEARCH_BASE}"
            f"?tab_id=home_tab"
            f"&refinement_paths%5B%5D=%2Fhomes"
            f"&room_types%5B%5D=Entire+home%2Fapt"
            f"&checkin={checkin}&checkout={checkout}"
            f"&adults=2&currency=THB&locale=en"
        )

    def _extract_from_html(self, html: str, season_tag: str) -> list[RawRentalListing]:
        """
        Parse listings from the niobeClientData SSR script tag.
        Airbnb embeds all search-result card data server-side — no API call needed.
        """
        m = re.search(r'<script[^>]*>\s*(\{"niobeClientData":.+?)</script>',
                      html, re.DOTALL)
        if not m:
            logger.warning("[Airbnb] niobeClientData script tag not found in HTML")
            return []

        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            logger.warning("[Airbnb] JSON parse error: %s", e)
            return []

        entries = data.get("niobeClientData", [])
        all_cards: list[dict] = []
        for entry in entries:
            if isinstance(entry, list) and len(entry) > 1:
                all_cards.extend(_find_card_nodes(entry[1]))

        logger.info("[Airbnb] season=%s: found %d card nodes", season_tag, len(all_cards))

        results = []
        for card in all_cards:
            listing = _parse_card(card, season_tag)
            if listing:
                results.append(listing)

        logger.info("[Airbnb] season=%s: parsed %d valid listings", season_tag, len(results))
        return results

    async def _scrape_one_season(self, season: str, checkin: str, checkout: str) -> list[RawRentalListing]:
        url = self._build_search_url(checkin, checkout)
        logger.info("[Airbnb] season=%s  checkin=%s", season, checkin)

        page = await self._new_page()
        ok = await self._goto(page, url)
        if not ok:
            await page.close()
            return []

        try:
            await page.wait_for_selector(
                '[data-testid="listing-card-title"], [itemprop="itemListElement"]',
                timeout=15_000,
            )
        except Exception:
            logger.warning("[Airbnb] Card selector timeout season=%s; continuing anyway", season)

        await page.wait_for_timeout(2_000)
        html = await page.content()
        await page.close()

        return self._extract_from_html(html, season)

    async def scrape(
        self,
        seasons: list[str] | None = None,
        max_pages: int = 1,
    ) -> AsyncIterator[RawRentalListing]:
        """Yield Airbnb listings for all requested seasons."""
        if seasons is None:
            seasons = list(_SEASON_DATES.keys())

        async with self:
            for i, season in enumerate(seasons):
                checkin, checkout = _SEASON_DATES[season]
                listings = await self._scrape_one_season(season, checkin, checkout)
                for listing in listings:
                    yield listing

                if i < len(seasons) - 1:
                    delay = 10.0 + __import__("random").uniform(0, 8)
                    logger.info("[Airbnb] Waiting %.0fs before next season...", delay)
                    await asyncio.sleep(delay)
