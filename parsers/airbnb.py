"""
Airbnb parser — https://www.airbnb.com/s/Phuket--Thailand/homes

Strategy:
  PRIMARY   — Intercept Airbnb's internal GraphQL/REST response
              (/api/v3/StaysSearch) while navigating the search page.
              The JSON contains clean structured listing data.
  FALLBACK  — Parse script tags for embedded JSON (niobeMinimalClientData
              or __NEXT_DATA__ depending on Airbnb's current stack).

Seasonal pricing:
  Airbnb prices depend on the check-in date. We run 4 passes (peak, high,
  shoulder, low) with fixed representative dates so we capture the full
  seasonal rate profile in one scrape() call.

Rate limiting / anti-bot:
  - Extra-long delays between season passes (10–20 s)
  - Random user-agent rotation per context
  - navigator.webdriver patched to undefined (from BaseParser)
  - Currency fixed to THB via URL param
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import AsyncIterator, Any

from bs4 import BeautifulSoup

from config import SCRAPER_HEADLESS
from parsers.base import BaseParser
from parsers.fazwaz_rental import RawRentalListing
from core.seasonality import classify_date

logger = logging.getLogger(__name__)

# Representative check-in / check-out dates for each Phuket season.
# Use dates ~1 year out so they're always bookable from today.
_SEASON_DATES: dict[str, tuple[str, str]] = {
    "peak":     ("2026-12-24", "2026-12-31"),   # Christmas week
    "high":     ("2027-02-10", "2027-02-17"),   # mid-Feb dry season
    "shoulder": ("2027-05-05", "2027-05-12"),   # May shoulder
    "low":      ("2027-07-08", "2027-07-15"),   # July monsoon
}

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
}

_TYPE_KW: dict[str, set[str]] = {
    "villa":  {"villa", "pool villa", "bungalow"},
    "house":  {"house", "townhouse", "cottage"},
    "condo":  {"condo", "condominium", "apartment", "studio", "flat"},
}


def _location_to_district(text: str) -> str:
    t = (text or "").lower()
    for key, slug in _LOCATION_MAP.items():
        if key in t:
            return slug
    return "phuket-other"


def _infer_property_type(name: str, room_type: str) -> str:
    """Infer property type from listing title, then room_type_category."""
    for text in [name.lower(), room_type.lower()]:
        for ptype, kws in _TYPE_KW.items():
            if any(kw in text for kw in kws):
                return ptype
    # Airbnb room_type_category: entire_home → condo by default for Phuket
    return "condo"


def _dig(obj: Any, *keys: str) -> Any:
    """Recursive key search — returns first value found at any depth."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
        for v in obj.values():
            result = _dig(v, *keys)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _dig(item, *keys)
            if result is not None:
                return result
    return None


def _extract_nightly_rate(pricing: Any, currency: str = "THB") -> float | None:
    """
    Walk various known Airbnb pricing structures to find the nightly amount.

    Known shapes (structure changes frequently):
      pricingQuote.rate.amount
      pricingQuote.structuredStayDisplayPrice.primaryLine.price
      pricingQuote.displayPrice.total.amount
    """
    if not pricing:
        return None

    # Shape 1 — simple rate.amount
    rate = _dig(pricing, "rate")
    if isinstance(rate, dict):
        amt = rate.get("amount") or rate.get("amountMicros")
        if amt:
            return float(amt) / 1_000_000 if rate.get("amountMicros") else float(amt)

    # Shape 2 — structuredStayDisplayPrice
    structured = _dig(pricing, "structuredStayDisplayPrice")
    if structured:
        primary = _dig(structured, "primaryLine")
        if primary:
            price_str = primary.get("price") or primary.get("displayComponentPrice")
            if price_str:
                m = re.search(r"([\d,]+)", str(price_str).replace(",", ""))
                if m:
                    return float(m.group(1))

    # Shape 3 — displayPrice.total.amount
    total = _dig(pricing, "total")
    if isinstance(total, dict):
        amt = total.get("amount") or total.get("formattedAmount")
        if amt:
            m = re.search(r"[\d,]+", str(amt).replace(",", ""))
            if m:
                return float(m.group())

    # Shape 4 — walk any key named "amount" with a plausible value
    amt = _dig(pricing, "amount")
    if amt and 100 < float(amt) < 1_000_000:
        return float(amt)

    return None


def _parse_listing(item: dict, season_tag: str) -> RawRentalListing | None:
    """Convert one Airbnb search-result item (listing + pricingQuote) → RawRentalListing."""
    listing = item.get("listing") or item
    if not listing or not isinstance(listing, dict):
        return None

    listing_id = str(listing.get("id") or listing.get("listingId") or "")
    if not listing_id:
        return None

    name = listing.get("name") or listing.get("title") or ""
    url = f"https://www.airbnb.com/rooms/{listing_id}"

    # ── Location ──────────────────────────────────────────────────────────
    location_text = " ".join(filter(None, [
        listing.get("city"),
        listing.get("neighborhood"),
        listing.get("publicAddress"),
        listing.get("localizedCityName"),
        _dig(listing, "locationTitle"),
    ]))
    district = _location_to_district(location_text or "phuket")

    coord = listing.get("coordinate") or {}
    lat = coord.get("latitude") or coord.get("lat")
    lon = coord.get("longitude") or coord.get("lng")

    # ── Property attributes ───────────────────────────────────────────────
    room_type = listing.get("roomTypeCategory") or listing.get("roomType") or ""
    property_type = _infer_property_type(name, room_type)

    bedrooms = listing.get("bedrooms")
    bathrooms = listing.get("bathrooms")
    guests = listing.get("personCapacity") or listing.get("maxGuestCapacity")

    # ── Amenities ─────────────────────────────────────────────────────────
    amenities_raw = listing.get("amenities") or listing.get("amenityIds") or []
    amenity_str = " ".join(str(a).lower() for a in amenities_raw)
    # Also check previewAmenities
    preview = listing.get("previewAmenities") or []
    preview_str = " ".join(str(a).lower() for a in preview)
    all_amenities = amenity_str + " " + preview_str + " " + name.lower()

    has_pool = bool(re.search(r"\bpool\b", all_amenities))
    has_gym  = bool(re.search(r"\bgym\b|\bfitness", all_amenities))
    has_view = bool(re.search(r"sea view|ocean view|seaview", all_amenities))

    # ── Demand signals ────────────────────────────────────────────────────
    rating = listing.get("avgRating") or listing.get("starRating")
    if rating is None:
        rating_str = listing.get("avgRatingLocalized") or ""
        m = re.search(r"([\d.]+)", str(rating_str))
        if m:
            rating = float(m.group(1))

    reviews = (listing.get("reviewsCount") or listing.get("numberOfReviews")
               or listing.get("visibleReviewCount"))

    # ── Price ─────────────────────────────────────────────────────────────
    pricing = item.get("pricingQuote") or item.get("pricing") or {}
    daily_rate = _extract_nightly_rate(pricing)

    if daily_rate is None or daily_rate <= 0:
        return None  # Skip listings without a valid price

    return RawRentalListing(
        source="airbnb",
        source_id=listing_id,
        url=url,
        project_name=name[:400] if name else None,
        district=district,
        property_type=property_type,
        bedrooms=int(bedrooms) if bedrooms is not None else None,
        bathrooms=int(bathrooms) if bathrooms is not None else None,
        area_sqm=None,                     # Airbnb doesn't expose sqm
        max_guests=int(guests) if guests is not None else None,
        has_pool=has_pool,
        has_gym=has_gym,
        has_sea_view=has_view,
        price_thb=daily_rate,
        price_period="nightly",
        daily_rate_thb=daily_rate,
        season_tag=season_tag,
        rental_type="short_term",          # Airbnb = short-term by definition
        platform_rating=float(rating) if rating is not None else None,
        platform_reviews_count=int(reviews) if reviews is not None else None,
        raw_data={
            "listing_id": listing_id,
            "name": name,
            "location": location_text,
            "room_type": room_type,
            "season": season_tag,
            "amenities_preview": preview[:5],
        },
        scraped_at=datetime.utcnow(),
    )


class AirbnbParser(BaseParser):
    SOURCE = "airbnb"

    _SEARCH_BASE = "https://www.airbnb.com/s/Phuket--Thailand/homes"
    # Intercept any URL containing these paths
    _API_PATTERNS = ["/api/v3/StaysSearch", "/api/v3/ExploreSearch",
                     "/api/v3/StaysPdpSections"]

    def _build_search_url(self, checkin: str, checkout: str, page_cursor: str = "") -> str:
        params = (
            f"?tab_id=home_tab"
            f"&refinement_paths%5B%5D=%2Fhomes"
            f"&room_types%5B%5D=Entire+home%2Fapt"
            f"&checkin={checkin}"
            f"&checkout={checkout}"
            f"&adults=2"
            f"&currency=THB"
            f"&locale=en"
        )
        return self._SEARCH_BASE + params

    def _extract_listings_from_json(self, data: Any) -> list[dict]:
        """
        Recursively search for arrays that look like listing result sets.
        Airbnb frequently restructures its response shape so we search broadly.
        """
        results = []

        def _walk(obj: Any) -> None:
            if isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict) and (
                        "listing" in item or "listingId" in item
                        or ("id" in item and "name" in item and "coordinate" in item)
                    ):
                        results.append(item)
                    else:
                        _walk(item)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _walk(v)

        _walk(data)
        return results

    def _extract_from_script_tags(self, html: str) -> list[dict]:
        """Fallback: search script tags for embedded JSON with listing arrays."""
        soup = BeautifulSoup(html, "lxml")
        candidates = []

        for script in soup.find_all("script"):
            text = script.string or ""
            # Look for Airbnb's data bootstrap patterns
            for pattern in [
                r'data-state="([^"]+)"',           # escaped JSON in attribute
                r'"niobeMinimalClientData":\[(.+?)\](?=,\n)',
                r'__NEXT_DATA__[^{]*({.+})',
            ]:
                if m := re.search(pattern, text, re.DOTALL):
                    try:
                        raw = m.group(1)
                        if raw.startswith('"'):
                            raw = raw.encode().decode("unicode_escape")
                        blob = json.loads(raw)
                        items = self._extract_listings_from_json(blob)
                        candidates.extend(items)
                    except Exception:
                        pass

            # Also check for large raw JSON blobs in script text
            if len(text) > 5000 and '"listing"' in text:
                try:
                    start = text.find("{")
                    if start != -1:
                        blob = json.loads(text[start:])
                        items = self._extract_listings_from_json(blob)
                        candidates.extend(items)
                except Exception:
                    pass

        return candidates

    async def _scrape_one_season(
        self,
        season: str,
        checkin: str,
        checkout: str,
        max_pages: int,
    ) -> list[RawRentalListing]:
        """Navigate Airbnb search for one check-in window, return all listings found."""
        results: list[RawRentalListing] = []
        intercepted_items: list[dict] = []

        url = self._build_search_url(checkin, checkout)
        logger.info("[Airbnb] season=%s  checkin=%s  url=%s", season, checkin, url)

        page = await self._new_page()

        # ── Intercept API responses ──────────────────────────────────────
        async def on_response(response):
            if any(p in response.url for p in self._API_PATTERNS):
                try:
                    body = await response.json()
                    items = self._extract_listings_from_json(body)
                    if items:
                        logger.debug("[Airbnb] Intercepted %d items from %s",
                                     len(items), response.url)
                        intercepted_items.extend(items)
                except Exception as exc:
                    logger.debug("[Airbnb] Response parse error: %s", exc)

        page.on("response", on_response)

        ok = await self._goto(page, url)
        if not ok:
            await page.close()
            return results

        # Give JS time to fire the API call and render
        try:
            await page.wait_for_selector('[data-testid="listing-card-title"]', timeout=12_000)
        except Exception:
            logger.warning("[Airbnb] Card selector timeout on season=%s; trying fallback", season)

        await page.wait_for_timeout(2_500)
        html = await page.content()
        await page.close()

        # ── Parse intercepted API items ──────────────────────────────────
        if intercepted_items:
            logger.info("[Airbnb] season=%s: parsing %d intercepted items",
                        season, len(intercepted_items))
            for item in intercepted_items:
                listing = _parse_listing(item, season)
                if listing:
                    results.append(listing)
        else:
            # ── Fallback: script-tag JSON ────────────────────────────────
            logger.info("[Airbnb] season=%s: no interception, trying script fallback", season)
            fallback_items = self._extract_from_script_tags(html)
            logger.info("[Airbnb] season=%s: fallback found %d items",
                        season, len(fallback_items))
            for item in fallback_items:
                listing = _parse_listing(item, season)
                if listing:
                    results.append(listing)

        logger.info("[Airbnb] season=%s: yielding %d listings", season, len(results))
        return results

    async def scrape(
        self,
        seasons: list[str] | None = None,
        max_pages: int = 1,
    ) -> AsyncIterator[RawRentalListing]:
        """
        Yield Airbnb rental listings for Phuket across multiple seasons.

        Each listing is yielded once per season it was found in, allowing
        callers to build a full seasonal rate profile per listing_id.

        seasons: subset of ["peak", "high", "shoulder", "low"] — defaults to all 4.
        max_pages: reserved for pagination (not yet implemented — Airbnb's
                   cursor-based pagination requires additional work).
        """
        if seasons is None:
            seasons = list(_SEASON_DATES.keys())

        async with self:
            for season in seasons:
                checkin, checkout = _SEASON_DATES[season]
                listings = await self._scrape_one_season(season, checkin, checkout, max_pages)
                for listing in listings:
                    yield listing

                if season != seasons[-1]:
                    # Longer pause between season passes to avoid rate limiting
                    delay = 12.0 + __import__("random").uniform(0, 8)
                    logger.info("[Airbnb] Waiting %.0fs before next season pass...", delay)
                    await asyncio.sleep(delay)
