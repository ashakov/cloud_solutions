"""
Normalizer — converts RawListing → db.models.Property and
recalculates DistrictBenchmark after each scraper run.
"""
import logging
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from db.models import Property, DistrictBenchmark
from parsers.base import RawListing

logger = logging.getLogger(__name__)


async def upsert_listing(session: AsyncSession, raw: RawListing) -> tuple[bool, bool]:
    """
    Insert or update a property listing.
    Returns (is_new, is_updated).
    """
    stmt = select(Property).where(
        Property.source == raw.source,
        Property.source_id == raw.source_id,
    )
    result = await session.execute(stmt)
    existing: Property | None = result.scalar_one_or_none()

    if existing is None:
        prop = Property(
            source=raw.source,
            source_id=raw.source_id,
            url=raw.url,
            project_name=raw.project_name,
            developer=raw.developer,
            unit_number=raw.unit_number,
            district=raw.district,
            subdistrict=raw.subdistrict,
            lat=raw.lat,
            lon=raw.lon,
            property_type=raw.property_type,
            ownership_type=raw.ownership_type,
            leasehold_years=raw.leasehold_years,
            title_deed=raw.title_deed,
            bedrooms=raw.bedrooms,
            bathrooms=raw.bathrooms,
            area_sqm=raw.area_sqm,
            land_sqm=raw.land_sqm,
            floor=raw.floor,
            total_floors=raw.total_floors,
            price_thb=raw.price_thb,
            price_per_sqm_thb=raw.price_per_sqm_thb,
            cam_fee_per_sqm=raw.cam_fee_per_sqm,
            sinking_fund_per_sqm=raw.sinking_fund_per_sqm,
            furniture_package_thb=raw.furniture_package_thb,
            rental_yield_claimed=raw.rental_yield_claimed,
            rental_program=raw.rental_program,
            has_hotel_license=raw.has_hotel_license,
            scraped_at=raw.scraped_at,
            updated_at=raw.scraped_at,
            is_active=True,
        )
        prop.set_raw(raw.raw_data)
        session.add(prop)
        return True, False

    # Update only fields that changed
    changed = False
    fields = [
        "url", "project_name", "developer", "district", "subdistrict",
        "lat", "lon", "property_type", "ownership_type", "leasehold_years",
        "bedrooms", "bathrooms", "area_sqm", "land_sqm", "floor", "total_floors",
        "price_thb", "price_per_sqm_thb", "cam_fee_per_sqm", "sinking_fund_per_sqm",
        "furniture_package_thb", "rental_yield_claimed", "rental_program",
        "has_hotel_license",
    ]
    for f in fields:
        new_val = getattr(raw, f, None)
        if new_val is not None and getattr(existing, f) != new_val:
            setattr(existing, f, new_val)
            changed = True

    if changed:
        existing.updated_at = datetime.utcnow()
        existing.set_raw(raw.raw_data)

    return False, changed


async def rebuild_benchmarks(session: AsyncSession) -> int:
    """
    Recalculate DistrictBenchmark from all active properties.
    Returns the number of benchmark rows written.
    """
    stmt = select(
        Property.district,
        Property.property_type,
        Property.ownership_type,
        Property.price_thb,
        Property.price_per_sqm_thb,
    ).where(
        Property.is_active == True,
        Property.price_thb > 0,
        Property.price_per_sqm_thb > 0,
    )
    rows = (await session.execute(stmt)).fetchall()

    if not rows:
        logger.warning("No active properties found for benchmark calculation")
        return 0

    df = pd.DataFrame(rows, columns=["district", "property_type", "ownership_type", "price_thb", "price_sqm"])

    # Delete stale benchmarks
    await session.execute(delete(DistrictBenchmark))

    written = 0
    for (district, prop_type, own_type), group in df.groupby(
        ["district", "property_type", "ownership_type"]
    ):
        sqm_vals = group["price_sqm"].dropna().values
        price_vals = group["price_thb"].dropna().values

        if len(sqm_vals) < 2:
            continue

        bm = DistrictBenchmark(
            district=district,
            property_type=prop_type,
            ownership_type=own_type,
            median_price_per_sqm=float(np.median(sqm_vals)),
            avg_price_per_sqm=float(np.mean(sqm_vals)),
            p25_price_per_sqm=float(np.percentile(sqm_vals, 25)),
            p75_price_per_sqm=float(np.percentile(sqm_vals, 75)),
            min_price_per_sqm=float(np.min(sqm_vals)),
            max_price_per_sqm=float(np.max(sqm_vals)),
            median_price_total=float(np.median(price_vals)) if len(price_vals) else None,
            avg_price_total=float(np.mean(price_vals)) if len(price_vals) else None,
            sample_count=len(sqm_vals),
            calculated_at=datetime.utcnow(),
        )
        session.add(bm)
        written += 1

    # Also compute "all" ownership aggregate
    for (district, prop_type), group in df.groupby(["district", "property_type"]):
        sqm_vals = group["price_sqm"].dropna().values
        price_vals = group["price_thb"].dropna().values

        if len(sqm_vals) < 3:
            continue

        bm = DistrictBenchmark(
            district=district,
            property_type=prop_type,
            ownership_type="all",
            median_price_per_sqm=float(np.median(sqm_vals)),
            avg_price_per_sqm=float(np.mean(sqm_vals)),
            p25_price_per_sqm=float(np.percentile(sqm_vals, 25)),
            p75_price_per_sqm=float(np.percentile(sqm_vals, 75)),
            min_price_per_sqm=float(np.min(sqm_vals)),
            max_price_per_sqm=float(np.max(sqm_vals)),
            median_price_total=float(np.median(price_vals)) if len(price_vals) else None,
            avg_price_total=float(np.mean(price_vals)) if len(price_vals) else None,
            sample_count=len(sqm_vals),
            calculated_at=datetime.utcnow(),
        )
        session.add(bm)
        written += 1

    await session.commit()
    logger.info("Benchmarks rebuilt: %d rows", written)
    return written
