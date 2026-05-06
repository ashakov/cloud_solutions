"""
Batch population of property_kpis and rental_calendar.

property_kpis:
    For each sale property with price + area, find the best rental comps
    in the same district/type, then run the KPI engine for all 3 scenarios.

    Comp matching hierarchy:
        1. district + property_type + bedrooms (±1)  → "direct_match"
        2. district + property_type                   → "district_type"
        3. district (any type)                        → "district_fallback"
        4. global Phuket median                       → "global_median"

rental_calendar:
    For each Airbnb listing with at least one nightly rate, generate one
    row per day for the next 365 days.  Season is classified from
    core.seasonality.classify_date; rate is picked from the matching
    daily_rate_*_thb column.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, date

import numpy as np
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Property, RentalListing, PropertyKPI, RentalCalendar
from core.kpi_engine import KPIParams, calculate_kpis
from core.seasonality import classify_date, implied_daily_rates

logger = logging.getLogger(__name__)

# ── KPI defaults ──────────────────────────────────────────────────────────────
_DEFAULT_CAM = 60.0          # THB / m² / month
_FURNITURE_DEFAULT = 150_000  # THB — base fitout assumption when not on listing


# ─────────────────────────────────────────────────────────────────────────────
# property_kpis
# ─────────────────────────────────────────────────────────────────────────────

async def populate_property_kpis(session: AsyncSession) -> int:
    """
    Calculate and store KPIs for all sale properties that have price + area.
    Deletes and recreates all rows on each run.
    Returns the number of (property_id, scenario) rows written.
    """
    # ── Load sale properties ─────────────────────────────────────────────────
    props = (await session.execute(
        select(Property).where(
            Property.price_thb.isnot(None),
            Property.area_sqm.isnot(None),
            Property.price_thb > 0,
            Property.area_sqm > 0,
            Property.is_active == True,
        )
    )).scalars().all()
    logger.info("[KPI] %d properties eligible", len(props))

    # ── Load rental comps index ──────────────────────────────────────────────
    rent_rows = (await session.execute(
        select(
            RentalListing.id,
            RentalListing.district,
            RentalListing.property_type,
            RentalListing.bedrooms,
            RentalListing.daily_rate_high_thb,
            RentalListing.monthly_rate_high_season_thb,
            RentalListing.monthly_rate_low_season_thb,
        ).where(RentalListing.is_active == True)
    )).fetchall()

    # ── Global fallback rates ────────────────────────────────────────────────
    all_daily = [r[4] for r in rent_rows if r[4]]
    all_monthly = [r[5] for r in rent_rows if r[5]]
    global_daily_high = float(np.median(all_daily)) if all_daily else 2000.0
    global_monthly_high = float(np.median(all_monthly)) if all_monthly else 50_000.0

    def _find_comps(district, prop_type, bedrooms):
        """Return (comps_list, method_str)."""
        exact = [r for r in rent_rows
                 if r[1] == district and r[2] == prop_type
                 and bedrooms is not None and r[3] is not None
                 and abs(r[3] - bedrooms) <= 1]
        if len(exact) >= 2:
            return exact, "direct_match"

        dt = [r for r in rent_rows if r[1] == district and r[2] == prop_type]
        if len(dt) >= 2:
            return dt, "district_type"

        dist = [r for r in rent_rows if r[1] == district]
        if len(dist) >= 2:
            return dist, "district_fallback"

        return rent_rows, "global_median"

    def _rates_from_comps(comps):
        """Derive seasonal daily rates from a set of comps."""
        daily_highs = [r[4] for r in comps if r[4]]
        monthly_highs = [r[5] for r in comps if r[5]]
        monthly_lows  = [r[6] for r in comps if r[6]]

        if daily_highs:
            med_high = float(np.median(daily_highs))
            med_low  = med_high * 0.65
            return implied_daily_rates(med_high * 30, med_low * 30), True
        elif monthly_highs:
            med_high = float(np.median(monthly_highs))
            med_low  = float(np.median(monthly_lows)) if monthly_lows else med_high * 0.7
            return implied_daily_rates(med_high, med_low), True
        else:
            return implied_daily_rates(global_daily_high * 30, global_daily_high * 30 * 0.65), False

    # ── Clear old KPIs ───────────────────────────────────────────────────────
    await session.execute(delete(PropertyKPI))

    written = 0
    for prop in props:
        comps, method = _find_comps(prop.district, prop.property_type, prop.bedrooms)
        rates, has_rates = _rates_from_comps(comps)

        best_comp_id = comps[0][0] if comps and method != "global_median" else None
        cam = prop.cam_fee_per_sqm or _DEFAULT_CAM
        furniture = _FURNITURE_DEFAULT

        for scenario in ("conservative", "base", "optimistic"):
            try:
                result = calculate_kpis(KPIParams(
                    purchase_price_thb=prop.price_thb,
                    area_sqm=prop.area_sqm,
                    furniture_cost_thb=furniture,
                    daily_peak_thb=rates["peak"],
                    daily_high_thb=rates["high"],
                    daily_shoulder_thb=rates["shoulder"],
                    daily_low_thb=rates["low"],
                    cam_per_sqm_month=cam,
                    scenario=scenario,
                ))
            except Exception as exc:
                logger.warning("[KPI] id=%d scenario=%s error: %s", prop.id, scenario, exc)
                continue

            kpi = PropertyKPI(
                property_id=prop.id,
                rental_listing_id=best_comp_id,
                comp_method=method,
                purchase_price_thb=prop.price_thb,
                transfer_fees_thb=result.total_investment_thb - prop.price_thb - furniture,
                furniture_cost_thb=furniture,
                total_investment_thb=result.total_investment_thb,
                daily_rate_peak_thb=rates["peak"],
                daily_rate_high_thb=rates["high"],
                daily_rate_shoulder_thb=rates["shoulder"],
                daily_rate_low_thb=rates["low"],
                occupancy_peak=0.92 if scenario == "base" else (0.96 if scenario == "optimistic" else 0.82),
                occupancy_high=0.80 if scenario == "base" else (0.88 if scenario == "optimistic" else 0.68),
                occupancy_shoulder=0.55 if scenario == "base" else (0.68 if scenario == "optimistic" else 0.40),
                occupancy_low=0.35 if scenario == "base" else (0.48 if scenario == "optimistic" else 0.22),
                gross_annual_revenue_thb=result.gross_annual_revenue_thb,
                management_fee_thb=result.management_fee_thb,
                cam_fee_annual_thb=result.cam_fee_annual_thb,
                sinking_fund_annual_thb=result.sinking_fund_annual_thb,
                land_tax_annual_thb=result.land_tax_annual_thb,
                income_tax_annual_thb=result.income_tax_annual_thb,
                insurance_annual_thb=result.insurance_annual_thb,
                maintenance_annual_thb=result.maintenance_annual_thb,
                total_opex_thb=result.total_opex_thb,
                net_annual_revenue_thb=result.net_annual_revenue_thb,
                gross_yield_pct=result.gross_yield_pct,
                net_yield_pct=result.net_yield_pct,
                breakeven_years=result.breakeven_years,
                roi_5yr_pct=result.roi_5yr_pct,
                roi_10yr_pct=result.roi_10yr_pct,
                scenario=scenario,
                calculated_at=datetime.utcnow(),
            )
            session.add(kpi)
            written += 1

        if written % 300 == 0 and written > 0:
            await session.commit()
            logger.info("[KPI] committed %d rows (prop id=%d)", written, prop.id)

    await session.commit()
    logger.info("[KPI] done. %d rows for %d properties", written, len(props))
    return written


# ─────────────────────────────────────────────────────────────────────────────
# rental_calendar
# ─────────────────────────────────────────────────────────────────────────────

_RATE_COL: dict[str, str] = {
    "peak":     "daily_rate_peak_thb",
    "high":     "daily_rate_high_thb",
    "shoulder": "daily_rate_shoulder_thb",
    "low":      "daily_rate_low_thb",
}


async def populate_rental_calendar(session: AsyncSession, days_ahead: int = 365) -> int:
    """
    Generate daily calendar entries for short-term (Airbnb) listings.
    Assumes all dates are available (is_available=True) — we only have
    pricing data, not real availability from search results.
    Clears and recreates on each call.
    Returns total rows written.
    """
    # Only short-term listings with at least one nightly rate
    listings = (await session.execute(
        select(RentalListing).where(
            RentalListing.rental_type == "short_term",
            RentalListing.daily_rate_high_thb.isnot(None),
        )
    )).scalars().all()
    logger.info("[Calendar] %d short-term listings", len(listings))

    await session.execute(delete(RentalCalendar))

    today = date.today()
    written = 0
    batch = []

    for listing in listings:
        rates = {
            "peak":     listing.daily_rate_peak_thb,
            "high":     listing.daily_rate_high_thb,
            "shoulder": listing.daily_rate_shoulder_thb,
            "low":      listing.daily_rate_low_thb,
        }
        # Fill missing season rates via interpolation
        high = rates["high"] or 0
        if not rates["peak"]:
            rates["peak"] = round(high * 1.55, 0)
        if not rates["shoulder"]:
            rates["shoulder"] = round(high * 0.75, 0)
        if not rates["low"]:
            rates["low"] = round(high * 0.55, 0)

        for offset in range(days_ahead):
            d = today + timedelta(days=offset)
            season = classify_date(d)
            rate = rates.get(season) or high
            if not rate:
                continue
            batch.append(RentalCalendar(
                rental_listing_id=listing.id,
                date=d,
                is_available=True,
                rate_thb=rate,
                season_tag=season,
            ))
            written += 1

            if len(batch) >= 2000:
                session.add_all(batch)
                await session.commit()
                batch.clear()
                logger.info("[Calendar] committed %d rows...", written)

    if batch:
        session.add_all(batch)
        await session.commit()

    logger.info("[Calendar] done. %d rows for %d listings", written, len(listings))
    return written
