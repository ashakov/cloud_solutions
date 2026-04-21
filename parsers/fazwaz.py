"""
FazWaz parser — https://www.fazwaz.com

Confirmed selectors (from live HTML inspection 2026-04):
  Card:     .result-search__item   (30 per page)
  Link:     a.link-unit
  Price:    .price-tag
  Title:    .unit-name
  Location: .location-unit
  Features: .unit-info__feature  (beds / baths / sqm in sequence)
  Ownership:.ownership-tooltip

URL:
  https://www.fazwaz.com/property-for-sale/thailand/phuket
    ?real_estate_type_id=1
    &search%5Barea_name%5D%5B0%5D=Bang+Tao
    &page=2
"""
import logging
import re
from typing import AsyncIterator
from urllib.parse import urljoin, quote_plus

from bs4 import BeautifulSoup

from config import FAZWAZ_BASE_URL, SCRAPER_MAX_PAGES
from parsers.base import BaseParser, RawListing

logger = logging.getLogger(__name__)


class FazWazParser(BaseParser):
    SOURCE = "fazwaz"

    _BASE_SEARCH = "https://www.fazwaz.com/property-for-sale/thailand/phuket"

    # real_estate_type_id values confirmed on site
    _TYPE_IDS = {"condo": "1", "villa": "2", "house": "3", "townhouse": "4", "land": "5"}

    # FazWaz canonical area names (used in search[area_name][0]=)
    _DISTRICT_NAMES = {
        "bang-tao":     "Bang Tao",
        "kamala":       "Kamala",
        "patong":       "Patong",
        "kata":         "Kata",
        "karon":        "Karon",
        "rawai":        "Rawai",
        "nai-harn":     "Nai Harn",
        "surin":        "Surin",
        "cherng-talay": "Cherng Talay",
        "mai-khao":     "Mai Khao",
        "chalong":      "Chalong",
        "layan":        "Layan",
        "laguna":       "Laguna",
        "phuket-town":  "Phuket Town",
    }

    _FREEHOLD_KW = {"freehold", "โฉนด", "chanote"}
    _LEASEHOLD_KW = {"leasehold", "สัญญาเช่า"}

    # Maps substrings in .location-unit text → canonical district slug
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
        "naiharn":        "nai-harn",
        "surin":          "surin",
        "choeng thale":   "cherng-talay",   # "Choeng Thale, Thalang" — most common
        "choeng thalay":  "cherng-talay",
        "cherng talay":   "cherng-talay",
        "cheng talay":    "cherng-talay",
        "si sunthon":     "layan",           # Si Sunthon subdistrict borders Layan/Bang Tao
        "mai khao":       "mai-khao",
        "maikhao":        "mai-khao",
        "sakhu":          "mai-khao",        # Sakhu subdistrict, north Phuket
        "pa khlok":       "pa-khlok",        # east-coast subdistrict
        "kathu":          "kathu",
        "chalong":        "chalong",
        "phuket town":    "phuket-town",
        "layan":          "layan",
        "thep krasattri": "thalang",         # true Thalang subdistrict
        "thalang":        "thalang",
    }

    def _location_to_district(self, text: str) -> str:
        t = text.lower()
        for key, slug in self._LOCATION_MAP.items():
            if key in t:
                return slug
        return "phuket-other"

    # Keywords to infer property_type from card title/text (server filter is broken)
    _TYPE_KW = {
        "villa":      {"villa", "pool villa", "private villa"},
        "house":      {"house", "detached house", "single house", "townhouse"},
        "land":       {"land", "plot", "rai"},
        "condo":      {"condo", "condominium", "apartment", "studio"},
    }

    def _detect_property_type(self, title: str, card_text: str) -> str:
        t = (title or "").lower() + " " + card_text[:300].lower()
        for ptype, kws in self._TYPE_KW.items():
            if any(kw in t for kw in kws):
                return ptype
        return "condo"  # safe default for Phuket

    def _build_url(self, page: int) -> str:
        """No type/district filter — FazWaz ignores them server-side."""
        return f"{self._BASE_SEARCH}?page={page}"

    async def scrape(
        self, district: str, property_type: str = "condo", max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawListing]:
        """Both district and property_type params are ignored in the URL — FazWaz
        server-side filters are broken.  District is parsed from .location-unit text
        and property_type is inferred from card title keywords."""

        async with self:
            for page_num in range(1, max_pages + 1):
                url = self._build_url(page_num)
                logger.info("[FazWaz] page %d — phuket (all types)", page_num)

                page = await self._new_page()
                ok = await self._goto(page, url)
                if not ok:
                    await page.close()
                    break

                # Wait for listing cards
                try:
                    await page.wait_for_selector(".result-search__item", timeout=15_000)
                except Exception:
                    logger.warning("[FazWaz] No cards on page %d", page_num)
                    await page.close()
                    break

                await page.wait_for_timeout(1_500)
                html = await page.content()
                await page.close()

                soup = BeautifulSoup(html, "lxml")
                cards = soup.select(".result-search__item")
                logger.info("[FazWaz] Found %d cards on page %d", len(cards), page_num)

                if not cards:
                    break

                for card in cards:
                    listing = self._parse_card(card, district, property_type)
                    if listing:
                        yield listing

                # Stop if fewer than expected (last page)
                if len(cards) < 10:
                    break

    def _parse_card(self, card: BeautifulSoup, district: str, property_type: str) -> RawListing | None:
        try:
            # ── URL & ID ──────────────────────────────────────────────────
            link_tag = card.select_one("a.link-unit") or card.find("a")
            if not link_tag:
                return None
            href = link_tag.get("href", "")
            if not href:
                return None
            url = href if href.startswith("http") else urljoin(FAZWAZ_BASE_URL, href)
            source_id = re.sub(r"[^a-zA-Z0-9_-]", "_", href.strip("/").split("/")[-1])[:250]

            # ── Price ─────────────────────────────────────────────────────
            # .price-tag can contain "฿6,700,000\n฿181,081/SqM" — take FIRST number only
            price_tag = card.select_one(".price-tag")
            price_raw = price_tag.get_text(" ", strip=True) if price_tag else None
            price_thb = self._parse_thb_first(price_raw)

            # ── Title / Project ───────────────────────────────────────────
            title_tag = card.select_one(".unit-name") or card.select_one(".unit-info_title")
            title = title_tag.get_text(strip=True) if title_tag else None

            # ── Location → parse real district from card text ─────────────
            loc_tag = card.select_one(".location-unit")
            location_text = loc_tag.get_text(strip=True) if loc_tag else ""
            district = self._location_to_district(location_text)

            # ── Property type — inferred from title+card (server filter broken) ──
            card_text_raw = card.get_text(" ", strip=True)
            property_type = self._detect_property_type(title or "", card_text_raw)

            # ── Features: beds / baths / sqm ─────────────────────────────
            # Strategy 1: .unit-info__shot-description — "1 Bed | 1 Bath | 35 SqM"
            bedrooms = bathrooms = None
            area_sqm = None
            desc = card.select_one(".unit-info__shot-description")
            if desc:
                desc_txt = desc.get_text(" ", strip=True)
                bed_m = re.search(r"(\d+)\s*(?:Bed|BR|Bedroom)", desc_txt, re.IGNORECASE)
                bath_m = re.search(r"(\d+)\s*(?:Bath|Bathroom)", desc_txt, re.IGNORECASE)
                sqm_m  = re.search(r"([\d,]+)\s*(?:SqM|Sq\.M|m²|sqm)", desc_txt, re.IGNORECASE)
                if bed_m:
                    bedrooms = int(bed_m.group(1))
                if bath_m:
                    bathrooms = int(bath_m.group(1))
                if sqm_m:
                    area_sqm = float(sqm_m.group(1).replace(",", ""))

            # Strategy 2: individual .unit-info__feature elements
            if not area_sqm:
                for feat in card.select(".unit-info__feature"):
                    txt = feat.get_text(" ", strip=True)
                    sqm_m = re.search(r"([\d,]+)\s*(?:SqM|Sq\.M|m²)", txt, re.IGNORECASE)
                    if sqm_m:
                        area_sqm = float(sqm_m.group(1).replace(",", ""))
                        break
                    if not bedrooms:
                        bed_m = re.search(r"(\d+)\s*(?:Bed|BR)", txt, re.IGNORECASE)
                        if bed_m:
                            bedrooms = int(bed_m.group(1))
                    if not bathrooms:
                        bath_m = re.search(r"(\d+)\s*(?:Bath)", txt, re.IGNORECASE)
                        if bath_m:
                            bathrooms = int(bath_m.group(1))

            # ── Ownership ─────────────────────────────────────────────────
            card_text = card_text_raw.lower()
            ownership = self._detect_ownership(card_text)
            leasehold_years = self._detect_leasehold_years(card_text) if ownership == "leasehold" else None

            # ── Tags (rental program, hotel licence) ─────────────────────
            tags = [t.get_text(strip=True).lower() for t in card.select(".manage-tag__item")]
            tag_text = " ".join(tags)
            rental_prog = bool(re.search(r"rental|guaranteed|return program", tag_text))
            has_hl = bool(re.search(r"hotel licen[sc]e|hotel permit", tag_text + " " + card_text))
            is_off_plan = bool(re.search(r"off.?plan|pre.?sale|under construction|pre-?launch", card_text))

            # ── Rental type ───────────────────────────────────────────────
            if re.search(r"short.?term|daily|weekly|airbnb", card_text):
                r_type = "short_term"
            elif re.search(r"long.?term|annual|monthly rent", card_text):
                r_type = "long_term"
            else:
                r_type = "unknown"

            # ── Pool / garden / gym ───────────────────────────────────────
            has_pool   = bool(re.search(r"\bpool\b|\bสระ", card_text))
            has_garden = bool(re.search(r"\bgarden\b|\bสวน", card_text))
            has_gym    = bool(re.search(r"\bgym\b|\bfitness", card_text))

            price_per_sqm = None
            if price_thb and area_sqm and area_sqm > 0:
                price_per_sqm = round(price_thb / area_sqm, 2)

            return RawListing(
                source=self.SOURCE,
                source_id=source_id,
                url=url,
                project_name=title,
                district=district,
                subdistrict=location_text[:100] if location_text else None,
                property_type=property_type,
                ownership_type=ownership,
                leasehold_years=leasehold_years,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                area_sqm=area_sqm,
                price_thb=price_thb,
                price_per_sqm_thb=price_per_sqm,
                rental_type=r_type,
                rental_program=rental_prog,
                has_hotel_license=has_hl,
                has_pool=has_pool,
                has_garden=has_garden,
                has_gym=has_gym,
                is_off_plan=is_off_plan,
                raw_data={
                    "card_text": card_text[:400],
                    "tags": tags,
                    "location_text": location_text,
                    "url": url,
                },
            )
        except Exception as exc:
            logger.exception("[FazWaz] card parse error: %s", exc)
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_thb_first(raw: str | None) -> float | None:
        """Extract FIRST price from text. Prevents concatenation when .price-tag
        contains both total price and price-per-sqm on separate lines."""
        if not raw:
            return None
        # Match: optional ฿, digits with commas, optional decimal, optional M/K suffix
        m = re.search(r"฿?\s*([\d,]+(?:\.\d+)?)\s*(M\b|K\b)?", raw.upper())
        if not m:
            return None
        digits = m.group(1).replace(",", "")
        suffix = m.group(2) or ""
        try:
            v = float(digits)
            if suffix == "M":
                v *= 1_000_000
            elif suffix == "K":
                v *= 1_000
            return v
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
        m = re.search(r"(\d+)\s*[-–]?\s*year\s+lease", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None
