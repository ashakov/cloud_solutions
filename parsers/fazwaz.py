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

    def _build_url(self, district: str, type_id: str, page: int) -> str:
        area_name = self._DISTRICT_NAMES.get(district, district.replace("-", " ").title())
        return (
            f"{self._BASE_SEARCH}"
            f"?real_estate_type_id={type_id}"
            f"&search%5Barea_name%5D%5B0%5D={quote_plus(area_name)}"
            f"&page={page}"
        )

    async def scrape(
        self, district: str, property_type: str = "condo", max_pages: int = SCRAPER_MAX_PAGES
    ) -> AsyncIterator[RawListing]:
        type_id = self._TYPE_IDS.get(property_type, "1")

        async with self:
            for page_num in range(1, max_pages + 1):
                url = self._build_url(district, type_id, page_num)
                logger.info("[FazWaz] page %d — %s/%s", page_num, district, property_type)

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
            price_tag = card.select_one(".price-tag")
            price_thb = self._parse_thb(price_tag.get_text(strip=True) if price_tag else None)

            # ── Title / Project ───────────────────────────────────────────
            title_tag = card.select_one(".unit-name") or card.select_one(".unit-info_title")
            title = title_tag.get_text(strip=True) if title_tag else None

            # ── Location ──────────────────────────────────────────────────
            loc_tag = card.select_one(".location-unit")
            location_text = loc_tag.get_text(strip=True) if loc_tag else ""

            # ── Features: beds / baths / sqm ─────────────────────────────
            features = card.select(".unit-info__feature")
            bedrooms = bathrooms = None
            area_sqm = None
            for feat in features:
                txt = feat.get_text(strip=True).lower()
                if "bed" in txt or "br" in txt:
                    bedrooms = self._parse_int(txt)
                elif "bath" in txt:
                    bathrooms = self._parse_int(txt)
                elif "sqm" in txt or "sq.m" in txt or "m²" in txt:
                    area_sqm = self._parse_float(re.sub(r"[^\d.]", "", txt.replace(",", "")))

            # Also try short description block
            if not bedrooms:
                desc = card.select_one(".unit-info__shot-description")
                if desc:
                    txt = desc.get_text(strip=True).lower()
                    bed_m = re.search(r"(\d+)\s*bed", txt)
                    bath_m = re.search(r"(\d+)\s*bath", txt)
                    sqm_m = re.search(r"([\d,]+)\s*sqm", txt)
                    if bed_m:
                        bedrooms = int(bed_m.group(1))
                    if bath_m:
                        bathrooms = int(bath_m.group(1))
                    if sqm_m:
                        area_sqm = float(sqm_m.group(1).replace(",", ""))

            # ── Ownership ─────────────────────────────────────────────────
            card_text = card.get_text(" ", strip=True).lower()
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
