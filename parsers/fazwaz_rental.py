"""
FazWaz rental parser — https://www.fazwaz.com/property-for-rent/

URL pattern (confirmed working):
  https://www.fazwaz.com/property-for-rent/thailand/phuket?page={n}

Selectors reuse the sale parser's confirmed classes (.result-search__item,
.price-tag, .unit-name, .location-unit, .unit-info__shot-description).

Rental-specific additions:
  - Rental type: short/long term inferred from min-stay and price period text
  - Monthly vs nightly rate: detected from price suffix ("/ month", "/ night")
  - Season classification: nightly rates tagged via core.seasonality
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import FAZWAZ_BASE_URL, SCRAPER_MAX_PAGES
from parsers.base import BaseParser
from core.seasonality import classify_date

logger = logging.getLogger(__name__)


@dataclass
class RawRentalListing:
    """Rental-specific scrape container — parallel to RawListing for sales."""
    source: str
    source_id: str
    url: str

    district: str | None = None
    subdistrict: str | None = None
    property_type: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    area_sqm: float | None = None
    max_guests: int | None = None

    # Amenities
    has_pool: bool = False
    has_gym: bool = False
    has_sea_view: bool = False
    has_parking: bool = False
    distance_to_beach_m: int | None = None

    # Pricing
    price_thb: float | None = None         # raw scraped price
    price_period: str | None = None        # "monthly" | "nightly" | "unknown"

    # Derived seasonal rates (set if period = nightly)
    daily_rate_thb: float | None = None    # same as price_thb if nightly
    season_tag: str | None = None          # peak|high|shoulder|low at scrape time

    # Long-term monthly (set if period = monthly)
    monthly_rate_thb: float | None = None

    # Terms
    min_stay_nights: int | None = None
    rental_type: str | None = None         # short_term | long_term | unknown

    # Demand signals
    platform_reviews_count: int | None = None
    platform_rating: float | None = None

    # Management
    has_management_company: bool = False
    management_fee_pct: float | None = None

    project_name: str | None = None
    raw_data: dict = field(default_factory=dict)
    scraped_at: datetime = field(default_factory=datetime.utcnow)


class FazWazRentalParser(BaseParser):
    SOURCE = "fazwaz_rent"

    _BASE_SEARCH = "https://www.fazwaz.com/property-for-rent/thailand/phuket"

    # Reuse location and type maps from sale parser
    _LOCATION_MAP: dict[str, str] = {
        "bang tao":       "bang-tao",
        "bangtao":        "bang-tao",
        "laguna":         "bang-tao",
        "kamala":         "kamala",
        "patong":         "patong",
        "kata":           "kata",
        "karon":          "karon",
        "rawai":          "rawai",
        "nai harn":       "nai-harn",
        "surin":          "surin",
        "choeng thale":   "cherng-talay",
        "si sunthon":     "layan",
        "mai khao":       "mai-khao",
        "sakhu":          "mai-khao",
        "pa khlok":       "pa-khlok",
        "kathu":          "kathu",
        "chalong":        "chalong",
        "phuket town":    "phuket-town",
        "layan":          "layan",
        "thep krasattri": "thalang",
        "thalang":        "thalang",
    }

    _TYPE_KW = {
        "villa":  {"villa", "pool villa"},
        "house":  {"house", "townhouse"},
        "land":   {"land", "plot"},
        "condo":  {"condo", "condominium", "apartment", "studio"},
    }

    def _location_to_district(self, text: str) -> str:
        t = text.lower()
        for key, slug in self._LOCATION_MAP.items():
            if key in t:
                return slug
        return "phuket-other"

    def _detect_property_type(self, title: str, card_text: str) -> str:
        # Title takes priority over generic card body text
        for text in [(title or "").lower(), card_text[:300].lower()]:
            for ptype, kws in self._TYPE_KW.items():
                if any(kw in text for kw in kws):
                    return ptype
        return "condo"

    @staticmethod
    def _parse_thb_first(raw: str | None) -> float | None:
        """Extract the first THB amount from a price string."""
        if not raw:
            return None
        m = re.search(r"([\d,]+(?:\.\d+)?)\s*(M\b|K\b)?", raw.upper())
        if not m:
            return None
        num = float(m.group(1).replace(",", ""))
        suffix = m.group(2) or ""
        if suffix == "M":
            num *= 1_000_000
        elif suffix == "K":
            num *= 1_000
        return num

    @staticmethod
    def _detect_price_period(raw: str | None) -> str:
        """Determine if the price is monthly, nightly, or unknown."""
        if not raw:
            return "unknown"
        t = raw.lower()
        if any(x in t for x in ["/month", "per month", "monthly", "/mo", "/ month"]):
            return "monthly"
        if any(x in t for x in ["/night", "per night", "nightly", "/day", "/ night"]):
            return "nightly"
        # FazWaz rental defaults to monthly if no suffix
        return "monthly"

    @staticmethod
    def _parse_min_stay(text: str) -> int | None:
        m = re.search(r"(\d+)\s*(?:month|months)", text.lower())
        if m:
            return int(m.group(1)) * 30
        m = re.search(r"(\d+)\s*(?:night|nights|day|days)", text.lower())
        if m:
            return int(m.group(1))
        return None

    def _build_url(self, page: int) -> str:
        return f"{self._BASE_SEARCH}?page={page}"

    async def scrape(
        self, max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawRentalListing]:
        """Scrape FazWaz rental listings for Phuket.
        No district/type filter — server ignores them; both are parsed per card."""
        async with self:
            for page_num in range(1, max_pages + 1):
                url = self._build_url(page_num)
                logger.info("[FazWazRent] page %d", page_num)

                page = await self._new_page()
                ok = await self._goto(page, url)
                if not ok:
                    await page.close()
                    break

                try:
                    await page.wait_for_selector(".result-search__item", timeout=15_000)
                except Exception:
                    logger.warning("[FazWazRent] No cards on page %d", page_num)
                    await page.close()
                    break

                await page.wait_for_timeout(1_500)
                html = await page.content()
                await page.close()

                soup = BeautifulSoup(html, "lxml")
                cards = soup.select(".result-search__item")
                logger.info("[FazWazRent] %d cards on page %d", len(cards), page_num)
                if not cards:
                    break

                for card in cards:
                    listing = self._parse_card(card)
                    if listing:
                        yield listing

                if len(cards) < 10:
                    break

    def _parse_card(self, card: BeautifulSoup) -> RawRentalListing | None:
        try:
            link_tag = card.select_one("a.link-unit") or card.find("a")
            if not link_tag:
                return None
            href = link_tag.get("href", "")
            if not href:
                return None
            url = href if href.startswith("http") else urljoin(FAZWAZ_BASE_URL, href)
            source_id = re.sub(r"[^a-zA-Z0-9_-]", "_", href.strip("/").split("/")[-1])[:250]

            # ── Price ────────────────────────────────────────────────────────
            price_tag = card.select_one(".price-tag")
            price_raw = price_tag.get_text(" ", strip=True) if price_tag else None
            price_thb = self._parse_thb_first(price_raw)
            price_period = self._detect_price_period(price_raw)

            # ── Title / project ──────────────────────────────────────────────
            title_tag = card.select_one(".unit-name") or card.select_one(".unit-info_title")
            title = title_tag.get_text(strip=True) if title_tag else None

            # ── Location ────────────────────────────────────────────────────
            loc_tag = card.select_one(".location-unit")
            location_text = loc_tag.get_text(strip=True) if loc_tag else ""
            district = self._location_to_district(location_text)

            # ── Beds / baths / sqm ──────────────────────────────────────────
            bedrooms = bathrooms = None
            area_sqm = None
            desc = card.select_one(".unit-info__shot-description")
            if desc:
                t = desc.get_text(" ", strip=True)
                if m := re.search(r"(\d+)\s*(?:Bed|BR)", t, re.IGNORECASE):
                    bedrooms = int(m.group(1))
                if m := re.search(r"(\d+)\s*(?:Bath)", t, re.IGNORECASE):
                    bathrooms = int(m.group(1))
                if m := re.search(r"([\d,]+)\s*(?:SqM|m²)", t, re.IGNORECASE):
                    area_sqm = float(m.group(1).replace(",", ""))

            if not area_sqm:
                for feat in card.select(".unit-info__feature"):
                    ft = feat.get_text(" ", strip=True)
                    if m := re.search(r"([\d,]+)\s*(?:SqM|m²)", ft, re.IGNORECASE):
                        area_sqm = float(m.group(1).replace(",", ""))
                        break

            # ── Card full text for keyword matching ──────────────────────────
            card_text = card.get_text(" ", strip=True)
            card_lower = card_text.lower()

            property_type = self._detect_property_type(title or "", card_text)

            # ── Amenities ────────────────────────────────────────────────────
            has_pool = bool(re.search(r"\bpool\b", card_lower))
            has_gym = bool(re.search(r"\bgym\b|\bfitness", card_lower))
            has_sea_view = bool(re.search(r"sea view|ocean view|seaview", card_lower))
            has_parking = bool(re.search(r"\bparking\b", card_lower))

            # ── Rental type & min stay ────────────────────────────────────────
            min_stay = self._parse_min_stay(card_text)
            if re.search(r"short.?term|daily|weekly|vacation", card_lower):
                rental_type = "short_term"
            elif min_stay and min_stay >= 30:
                rental_type = "long_term"
            elif price_period == "nightly":
                rental_type = "short_term"
            elif price_period == "monthly":
                rental_type = "long_term"
            else:
                rental_type = "unknown"

            # ── Season tag at scrape time ────────────────────────────────────
            from datetime import date
            season_tag = classify_date(date.today())

            # ── Distance to beach (rough text parse) ─────────────────────────
            beach_m = None
            if m := re.search(r"(\d[\d,.]*)\s*(km|m)\s*(?:from|to)\s*(?:the\s+)?beach",
                               card_lower):
                val = float(m.group(1).replace(",", ""))
                beach_m = int(val * 1000 if m.group(2) == "km" else val)

            # ── Management company ────────────────────────────────────────────
            has_mgmt = bool(re.search(r"management company|managed by|rental program|guaranteed",
                                       card_lower))

            daily = price_thb if price_period == "nightly" else None
            monthly = price_thb if price_period == "monthly" else None

            return RawRentalListing(
                source=self.SOURCE,
                source_id=source_id,
                url=url,
                project_name=title,
                district=district,
                property_type=property_type,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                area_sqm=area_sqm,
                has_pool=has_pool,
                has_gym=has_gym,
                has_sea_view=has_sea_view,
                has_parking=has_parking,
                distance_to_beach_m=beach_m,
                price_thb=price_thb,
                price_period=price_period,
                daily_rate_thb=daily,
                monthly_rate_thb=monthly,
                season_tag=season_tag,
                min_stay_nights=min_stay,
                rental_type=rental_type,
                has_management_company=has_mgmt,
                raw_data={"title": title, "location": location_text,
                          "price_raw": price_raw, "card_snippet": card_text[:400]},
            )
        except Exception as exc:
            logger.debug("[FazWazRent] card parse error: %s", exc)
            return None
