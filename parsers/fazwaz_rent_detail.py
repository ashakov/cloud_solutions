"""
FazWaz rental detail-page enricher.

Visits each rental listing that is missing lat/lon and fills in:
  - lat / lon         from Google Maps Street View link
  - distance_to_beach_m  from .project-information-info-place-distance

Same selector logic as fazwaz_detail.py — FazWaz uses the same page template
for both sale and rental detail pages.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from parsers.base import BaseParser

logger = logging.getLogger(__name__)


def _extract_location(html: str, soup: BeautifulSoup) -> dict:
    result: dict = {}

    # Coordinates from Google Maps Street View link
    coord_m = re.search(r"viewpoint=([\d.]+),([\d.]+)", html)
    if coord_m:
        lat, lon = float(coord_m.group(1)), float(coord_m.group(2))
        if 7.4 <= lat <= 8.3 and 97.9 <= lon <= 98.9:
            result["lat"] = lat
            result["lon"] = lon

    # Distance to nearest beach
    dist_el = soup.select_one(".project-information-info-place-distance")
    if dist_el:
        dm = re.search(r"([\d.]+)\s*(km|m)\b", dist_el.get_text(strip=True), re.IGNORECASE)
        if dm:
            val = float(dm.group(1))
            if dm.group(2).lower() == "km":
                val *= 1000
            result["distance_to_beach_m"] = int(val)

    return result


class FazWazRentDetailScraper(BaseParser):
    SOURCE = "fazwaz_rent"

    async def scrape(self, *args, **kwargs):
        return
        yield

    async def enrich(
        self,
        db_session,
        *,
        batch_size: int = 10,
        delay: float = 2.5,
        limit: int | None = None,
    ) -> tuple[int, int]:
        """
        Visit rental listings missing lat and fill in lat/lon + distance_to_beach.
        Returns (total_visited, total_updated).
        """
        from db.models import RentalListing
        from sqlalchemy import select

        q = (
            select(RentalListing)
            .where(RentalListing.lat.is_(None))
            .where(RentalListing.source == "fazwaz_rent")
            .order_by(RentalListing.id)
        )
        if limit:
            q = q.limit(limit)

        rows = (await db_session.execute(q)).scalars().all()
        total = len(rows)
        logger.info("[FazWazRentDetail] %d rental listings to enrich", total)

        visited = updated = 0
        async with self:
            for i, listing in enumerate(rows):
                page = await self._new_page()
                ok = await self._goto(page, listing.url)
                if not ok:
                    await page.close()
                    logger.warning(
                        "[FazWazRentDetail] %d/%d SKIP (load failed): %s",
                        i + 1, total, listing.url,
                    )
                    visited += 1
                    continue

                await page.wait_for_timeout(2000)
                html = await page.content()
                await page.close()

                soup = BeautifulSoup(html, "lxml")
                fields = _extract_location(html, soup)
                visited += 1

                if fields:
                    for k, v in fields.items():
                        setattr(listing, k, v)
                    listing.updated_at = datetime.utcnow()
                    updated += 1
                    logger.info(
                        "[FazWazRentDetail] %d/%d updated id=%d: %s",
                        i + 1, total, listing.id, fields,
                    )
                else:
                    logger.warning(
                        "[FazWazRentDetail] %d/%d no location fields: %s",
                        i + 1, total, listing.url,
                    )

                if updated % batch_size == 0 and updated > 0:
                    await db_session.commit()
                    logger.info("[FazWazRentDetail] committed %d/%d", updated, total)

                if i < total - 1:
                    await asyncio.sleep(delay)

        await db_session.commit()
        logger.info("[FazWazRentDetail] done. visited=%d updated=%d", visited, updated)
        return visited, updated
