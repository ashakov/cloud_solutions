"""
FazWaz property detail-page enricher.

Visits each property URL that is missing key fields (area_sqm, bedrooms,
ownership_type, etc.) and fills them from the detail page.

Confirmed selectors (2026-04):
  Price:      .unit-sale-price__header-price  → "฿4,600,000"
  Beds:       i.icon-bed-resale-rental parent → "1 Beds"
  Baths:      i.icon-bath-resale-rental parent → "1 Baths"
  SqM:        i.icon-size-resale-rental parent → "39.70 SqM Size"
  Info block: .basic-information full text   → "Floor 7 Bedroom 1 Size 39.70 SqM Price per SqM ฿115,869 ..."
  Header:     .header-detail-page-desktop    → "...7 Floor Mar 2027 Off Plan" / "8% Rental Yield"
  CAM fee:    page text                      → "฿70 per square meter"
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from parsers.base import BaseParser

logger = logging.getLogger(__name__)


def _n(text: str | None, pattern: str, group: int = 1, flags: int = re.IGNORECASE) -> str | None:
    if not text:
        return None
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def _thb(text: str | None) -> float | None:
    v = _n(text, r"฿\s*([\d,]+(?:\.\d+)?)")
    return float(v.replace(",", "")) if v else None


class FazWazDetailScraper(BaseParser):
    SOURCE = "fazwaz"

    async def scrape(self, *args, **kwargs):  # satisfies BaseParser abstract method
        return
        yield  # make it an async generator

    # Fields worth re-enriching even if already set
    _ALWAYS_REFRESH = {"price_thb", "price_per_sqm_thb"}

    def _parse_detail(self, html: str) -> dict:
        """Parse a single detail page → dict of fields to update."""
        soup = BeautifulSoup(html, "lxml")
        result: dict = {}

        # ── Price ──────────────────────────────────────────────────────────
        price_el = (soup.select_one(".unit-sale-price__header-price")
                    or soup.select_one(".price-message")
                    or soup.select_one(".resale-rental-full-price"))
        if price_el:
            result["price_thb"] = _thb(price_el.get_text())

        # ── Beds / Baths / SqM from header icons ───────────────────────────
        bed_el = soup.select_one("i.icon-bed-resale-rental")
        if bed_el:
            t = bed_el.find_parent().get_text(" ", strip=True)
            v = _n(t, r"(\d+)\s*Bed")
            if v:
                result["bedrooms"] = int(v)

        bath_el = soup.select_one("i.icon-bath-resale-rental")
        if bath_el:
            t = bath_el.find_parent().get_text(" ", strip=True)
            v = _n(t, r"(\d+)\s*Bath")
            if v:
                result["bathrooms"] = int(v)

        size_el = soup.select_one("i.icon-size-resale-rental")
        if size_el:
            t = size_el.find_parent().get_text(" ", strip=True)
            v = _n(t, r"([\d.,]+)\s*SqM")
            if v:
                result["area_sqm"] = float(v.replace(",", ""))

        # ── Header block: floor, completion, off-plan, rental yield ────────
        hdr_el = (soup.select_one(".header-detail-page-desktop")
                  or soup.select_one(".header-detail-page"))
        hdr_text = hdr_el.get_text(" ", strip=True) if hdr_el else ""

        floor_v = _n(hdr_text, r"(\d+)\s*Floor")
        if floor_v:
            result["floor"] = int(floor_v)

        # "Mar 2015 Completed" → year_built=2015, is_off_plan=False
        # "Mar 2027 Off Plan"  → completion_date, is_off_plan=True
        comp_m = re.search(
            r"([A-Z][a-z]{2}\s+\d{4})\s+(Completed|Off Plan)", hdr_text, re.IGNORECASE
        )
        if comp_m:
            date_str, status = comp_m.group(1), comp_m.group(2).lower()
            result["is_off_plan"] = "off plan" in status
            try:
                dt = datetime.strptime(date_str, "%b %Y")
                if "off plan" in status:
                    result["completion_date"] = dt
                else:
                    result["year_built"] = dt.year
            except ValueError:
                pass

        yield_v = _n(hdr_text, r"(\d+(?:\.\d+)?)\s*%\s*Rental\s*Yield")
        if yield_v:
            result["rental_yield_claimed"] = float(yield_v) / 100

        # ── Basic information block: floor, sqm, price/sqm, type ───────────
        bi = soup.select_one(".basic-information")
        if bi:
            bi_text = bi.get_text(" ", strip=True)

            if "floor" not in result:
                v = _n(bi_text, r"Floor\s+(\d+)")
                if v:
                    result["floor"] = int(v)

            if "area_sqm" not in result:
                v = _n(bi_text, r"Size\s+([\d.,]+)\s*SqM")
                if v:
                    result["area_sqm"] = float(v.replace(",", ""))

            v = _n(bi_text, r"Price per SqM\s+฿([\d,]+)")
            if v:
                result["price_per_sqm_thb"] = float(v.replace(",", ""))

            # Ownership: leasehold wins; freehold as default if condo
            bi_lower = bi_text.lower()
            if "leasehold" in bi_lower:
                result["ownership_type"] = "leasehold"
                yrs = _n(bi_lower, r"(\d+)[\s-]*year\s+lease")
                if yrs:
                    result["leasehold_years"] = int(yrs)
            elif "freehold" in bi_lower or "chanote" in bi_lower:
                result["ownership_type"] = "freehold"

        # ── CAM fee from page text ──────────────────────────────────────────
        # "฿70 per square meter" or "฿1,985/mo" in basic-information-info
        page_text = soup.get_text(" ", strip=True)
        cam_m = re.search(
            r"฿\s*([\d,]+(?:\.\d+)?)\s+per\s+square\s+meter", page_text, re.IGNORECASE
        )
        if cam_m:
            result["cam_fee_per_sqm"] = float(cam_m.group(1).replace(",", ""))

        # ── Rental program / hotel license ──────────────────────────────────
        page_lower = page_text.lower()
        if re.search(r"guaranteed\s+rental|rental\s+program|rental\s+pool", page_lower):
            result["rental_program"] = True
        if re.search(r"hotel\s+licen[sc]e|hotel\s+permit", page_lower):
            result["has_hotel_license"] = True

        # ── Price/sqm fallback ──────────────────────────────────────────────
        if result.get("price_thb") and result.get("area_sqm") and "price_per_sqm_thb" not in result:
            sqm = result["area_sqm"]
            if sqm > 0:
                result["price_per_sqm_thb"] = round(result["price_thb"] / sqm, 0)

        # ── Coordinates from Google Maps Street View link ───────────────────
        # href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=7.9803254,98.3556413&..."
        coord_m = re.search(r'viewpoint=([\d.]+),([\d.]+)', html)
        if coord_m:
            lat, lon = float(coord_m.group(1)), float(coord_m.group(2))
            # Sanity-check: Phuket bounding box
            if 7.4 <= lat <= 8.3 and 97.9 <= lon <= 98.9:
                result["lat"] = lat
                result["lon"] = lon

        # ── Distance to nearest beach ───────────────────────────────────────
        # .project-information-info-place-distance → "- 0.5 Km" or "- 400 M"
        dist_el = soup.select_one(".project-information-info-place-distance")
        if dist_el:
            dm = re.search(r"([\d.]+)\s*(km|m)\b", dist_el.get_text(strip=True), re.IGNORECASE)
            if dm:
                val = float(dm.group(1))
                if dm.group(2).lower() == "km":
                    val *= 1000
                result["distance_to_beach_m"] = int(val)

        return result

    async def enrich(
        self,
        db_session,
        *,
        batch_size: int = 10,
        delay: float = 2.5,
        limit: int | None = None,
    ) -> tuple[int, int]:
        """
        Fetch all properties missing area_sqm, visit each URL, update DB.
        Returns (total_visited, total_updated).
        """
        from db.models import Property
        from sqlalchemy import select

        q = select(Property).where(Property.lat.is_(None)).order_by(Property.id)
        if limit:
            q = q.limit(limit)

        rows = (await db_session.execute(q)).scalars().all()
        total = len(rows)
        logger.info("[FazWazDetail] %d properties to enrich", total)

        visited = updated = 0
        async with self:
            for i, prop in enumerate(rows):
                page = await self._new_page()
                ok = await self._goto(page, prop.url)
                if not ok:
                    await page.close()
                    logger.warning("[FazWazDetail] %d/%d SKIP (load failed): %s",
                                   i + 1, total, prop.url)
                    visited += 1
                    continue

                await page.wait_for_timeout(2000)
                html = await page.content()
                await page.close()

                fields = self._parse_detail(html)
                visited += 1

                if fields:
                    for k, v in fields.items():
                        setattr(prop, k, v)
                    prop.updated_at = datetime.utcnow()
                    updated += 1
                    logger.info(
                        "[FazWazDetail] %d/%d updated id=%d: %s",
                        i + 1, total, prop.id,
                        {k: v for k, v in fields.items()
                         if k not in ("raw_data",)},
                    )
                else:
                    logger.warning("[FazWazDetail] %d/%d no fields extracted: %s",
                                   i + 1, total, prop.url)

                if updated % batch_size == 0 and updated > 0:
                    await db_session.commit()
                    logger.info("[FazWazDetail] committed %d/%d", updated, total)

                if i < total - 1:
                    await asyncio.sleep(delay)

        await db_session.commit()
        logger.info("[FazWazDetail] done. visited=%d updated=%d", visited, updated)
        return visited, updated
