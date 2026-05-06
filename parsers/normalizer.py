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

from db.models import Property, DistrictBenchmark, PriceHistory
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
            monthly_rent_thb=raw.monthly_rent_thb,
            rental_type=raw.rental_type,
            rental_yield_claimed=raw.rental_yield_claimed,
            rental_program=raw.rental_program,
            rental_pool_split=raw.rental_pool_split,
            has_hotel_license=raw.has_hotel_license,
            has_pool=raw.has_pool,
            has_garden=raw.has_garden,
            has_gym=raw.has_gym,
            parking_spaces=raw.parking_spaces,
            occupancy_status=raw.occupancy_status,
            is_off_plan=raw.is_off_plan,
            completion_date=raw.completion_date,
            year_built=raw.year_built,
            days_on_market=raw.days_on_market,
            price_drop_count=raw.price_drop_count,
            zone_type=raw.zone_type,
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
        "title_deed", "bedrooms", "bathrooms", "area_sqm", "land_sqm",
        "floor", "total_floors", "price_thb", "price_per_sqm_thb",
        "cam_fee_per_sqm", "sinking_fund_per_sqm", "furniture_package_thb",
        "monthly_rent_thb", "rental_type", "rental_yield_claimed",
        "rental_program", "rental_pool_split", "has_hotel_license",
        "has_pool", "has_garden", "has_gym", "parking_spaces",
        "occupancy_status", "is_off_plan", "completion_date", "year_built",
        "days_on_market", "price_drop_count", "zone_type",
    ]
    old_price = existing.price_thb
    for f in fields:
        new_val = getattr(raw, f, None)
        if new_val is not None and getattr(existing, f) != new_val:
            setattr(existing, f, new_val)
            changed = True

    if changed:
        existing.updated_at = datetime.utcnow()
        existing.set_raw(raw.raw_data)

        # Record price change in history if price moved
        new_price = raw.price_thb
        if new_price and new_price != old_price:
            status = "price_drop" if (old_price and new_price < old_price) else "price_increase"
            session.add(PriceHistory(
                property_id=existing.id,
                price_thb=new_price,
                price_per_sqm_thb=raw.price_per_sqm_thb,
                status=status,
            ))

    return False, changed


async def rebuild_benchmarks(session: AsyncSession) -> int:
    """
    Recalculate DistrictBenchmark from all active properties + rental comps.
    Returns the number of benchmark rows written.
    """
    from db.models import RentalListing
    from core.seasonality import annual_revenue, OCCUPANCY, SEASON_DAYS

    # ── Sale data ────────────────────────────────────────────────────────────
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

    # ── Rental data ──────────────────────────────────────────────────────────
    rent_stmt = select(
        RentalListing.district,
        RentalListing.property_type,
        RentalListing.daily_rate_high_thb,
        RentalListing.monthly_rate_high_season_thb,
        RentalListing.monthly_rate_low_season_thb,
    ).where(RentalListing.is_active == True)
    rent_rows = (await session.execute(rent_stmt)).fetchall()
    rent_df = pd.DataFrame(rent_rows, columns=[
        "district", "property_type", "daily_high", "monthly_high", "monthly_low",
    ])

    # Weighted avg occupancy for base scenario (constant across districts)
    occ = OCCUPANCY["base"]
    occupancy_avg = round(
        sum(SEASON_DAYS[s] * occ[s] for s in occ) / sum(SEASON_DAYS.values()), 4
    )

    def _rental_metrics(district: str, prop_type: str | None) -> dict:
        """Compute rental metrics for a district/type slice."""
        mask = rent_df["district"] == district
        if prop_type:
            mask &= rent_df["property_type"] == prop_type
        sub = rent_df[mask]
        if sub.empty:
            return {}

        daily_vals = sub["daily_high"].dropna()
        monthly_vals = sub["monthly_high"].dropna()

        result: dict = {"sample_count_rentals": len(sub), "occupancy_rate_avg": occupancy_avg}

        if not daily_vals.empty:
            med_daily = float(np.median(daily_vals))
            result["avg_daily_rate_high_thb"] = med_daily
        if not monthly_vals.empty:
            result["avg_monthly_rate_thb"] = float(np.median(monthly_vals))

        # Implied gross yield: annualise the best available rate, divide by median sale price
        sale_mask = (df["district"] == district)
        if prop_type:
            sale_mask &= (df["property_type"] == prop_type)
        sale_prices = df[sale_mask]["price_thb"].dropna()
        if sale_prices.empty:
            return result

        med_sale = float(np.median(sale_prices))
        if med_sale <= 0:
            return result

        if not daily_vals.empty:
            med_daily = float(np.median(daily_vals))
            monthly_low = med_daily * 30 * 0.65
            gross, _ = annual_revenue(
                med_daily * 1.55, med_daily, med_daily * 0.75, monthly_low / 30, scenario="base"
            )
        elif not monthly_vals.empty:
            med_monthly = float(np.median(monthly_vals))
            med_low = float(np.median(sub["monthly_low"].dropna())) if not sub["monthly_low"].dropna().empty else med_monthly * 0.7
            gross = med_monthly * 6 + med_low * 6
        else:
            return result

        result["avg_rental_yield_pct"] = round(gross / med_sale * 100, 4)
        return result

    # ── Delete stale benchmarks ───────────────────────────────────────────────
    await session.execute(delete(DistrictBenchmark))

    def _make_bm(district, prop_type, own_type, sqm_vals, price_vals) -> DistrictBenchmark:
        metrics = _rental_metrics(district, prop_type)
        return DistrictBenchmark(
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
            avg_daily_rate_high_thb=metrics.get("avg_daily_rate_high_thb"),
            avg_monthly_rate_thb=metrics.get("avg_monthly_rate_thb"),
            avg_rental_yield_pct=metrics.get("avg_rental_yield_pct"),
            occupancy_rate_avg=metrics.get("occupancy_rate_avg"),
            sample_count_rentals=metrics.get("sample_count_rentals"),
            calculated_at=datetime.utcnow(),
        )

    written = 0
    for (district, prop_type, own_type), group in df.groupby(
        ["district", "property_type", "ownership_type"]
    ):
        sqm_vals = group["price_sqm"].dropna().values
        price_vals = group["price_thb"].dropna().values
        if len(sqm_vals) < 2:
            continue
        session.add(_make_bm(district, prop_type, own_type, sqm_vals, price_vals))
        written += 1

    # "all" ownership aggregate
    for (district, prop_type), group in df.groupby(["district", "property_type"]):
        sqm_vals = group["price_sqm"].dropna().values
        price_vals = group["price_thb"].dropna().values
        if len(sqm_vals) < 3:
            continue
        session.add(_make_bm(district, prop_type, "all", sqm_vals, price_vals))
        written += 1

    await session.commit()
    logger.info("Benchmarks rebuilt: %d rows", written)
    return written
