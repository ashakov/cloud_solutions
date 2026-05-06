"""Dump full rental_listings and property tables to stdout."""
import sys, asyncio, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from db.database import AsyncSessionLocal
from db.models import RentalListing, Property
from sqlalchemy import select, func, text


async def dump():
    async with AsyncSessionLocal() as s:

        # ── SUMMARY ──────────────────────────────────────────────────────────
        total_rent = (await s.execute(select(func.count()).select_from(RentalListing))).scalar()
        total_sale = (await s.execute(select(func.count()).select_from(Property))).scalar()
        print(f"{'='*70}")
        print(f" DATABASE CONTENTS")
        print(f"{'='*70}")
        print(f" rental_listings : {total_rent} rows")
        print(f" properties      : {total_sale} rows")
        print()

        # ── RENTAL LISTINGS — full table ─────────────────────────────────────
        print(f"{'='*70}")
        print(" RENTAL LISTINGS")
        print(f"{'='*70}")

        rows = (await s.execute(
            select(RentalListing)
            .order_by(RentalListing.source, RentalListing.district, RentalListing.property_type)
        )).scalars().all()

        # Header
        print(f"{'#':>4}  {'source':<13} {'district':<18} {'type':<8} {'BR':>3} {'sqm':>6}"
              f"  {'peak':>7} {'high':>7} {'shldr':>7} {'low':>7}"
              f"  {'mo_high':>8}  {'rating':>6}  name")
        print("  " + "-" * 115)

        for i, r in enumerate(rows, 1):
            # name from raw_data JSON
            try:
                raw = json.loads(r.raw_data or "{}")
                name = (raw.get("name") or raw.get("title") or "")[:32]
            except Exception:
                name = ""

            def fmt(v):
                return f"{v:>7,.0f}" if v else "      -"

            br   = str(r.bedrooms) if r.bedrooms is not None else " -"
            sqm  = f"{r.area_sqm:>6.0f}" if r.area_sqm else "     -"
            rat  = f"{r.platform_rating:.2f}" if r.platform_rating else "     -"
            mo   = f"{r.monthly_rate_high_season_thb:>8,.0f}" if r.monthly_rate_high_season_thb else "       -"
            tags = []
            if r.has_pool:     tags.append("P")
            if r.has_sea_view: tags.append("S")
            if r.has_gym:      tags.append("G")
            tag  = "".join(tags) if tags else " "

            print(f"{i:>4}  {r.source:<13} {r.district:<18} {r.property_type:<8} {br:>3} {sqm}"
                  f"  {fmt(r.daily_rate_peak_thb)} {fmt(r.daily_rate_high_thb)}"
                  f" {fmt(r.daily_rate_shoulder_thb)} {fmt(r.daily_rate_low_thb)}"
                  f"  {mo}  {rat}  {tag}  {name}")

        # ── RENTAL STATS ─────────────────────────────────────────────────────
        print()
        print(f"{'='*70}")
        print(" RENTAL STATS BY DISTRICT + TYPE")
        print(f"{'='*70}")

        rows = (await s.execute(text("""
            SELECT
                source,
                district,
                property_type,
                COUNT(*) as n,
                ROUND(AVG(bedrooms), 1) as avg_br,
                ROUND(AVG(area_sqm), 0) as avg_sqm,
                ROUND(AVG(daily_rate_peak_thb), 0) as avg_peak,
                ROUND(AVG(daily_rate_high_thb), 0) as avg_high,
                ROUND(AVG(monthly_rate_high_season_thb), 0) as avg_mo,
                ROUND(AVG(platform_rating), 2) as avg_rating
            FROM rental_listings
            GROUP BY source, district, property_type
            ORDER BY source, district, property_type
        """))).fetchall()

        print(f"{'source':<13} {'district':<18} {'type':<8} {'n':>4}  "
              f"{'avg_br':>6} {'avg_sqm':>7}  {'avg_peak':>8} {'avg_high':>8} {'avg_mo':>9}  {'rating':>6}")
        print("  " + "-" * 100)
        for r in rows:
            src, dist, ptype, n, avg_br, avg_sqm, avg_peak, avg_high, avg_mo, avg_rat = r
            br_s   = f"{avg_br:.1f}" if avg_br else "   -"
            sqm_s  = f"{avg_sqm:>7.0f}" if avg_sqm else "      -"
            peak_s = f"{avg_peak:>8,.0f}" if avg_peak else "       -"
            high_s = f"{avg_high:>8,.0f}" if avg_high else "       -"
            mo_s   = f"{avg_mo:>9,.0f}" if avg_mo else "        -"
            rat_s  = f"{avg_rat:.2f}" if avg_rat else "   -"
            print(f"{src:<13} {dist:<18} {ptype:<8} {n:>4}  "
                  f"{br_s:>6} {sqm_s}  {peak_s} {high_s} {mo_s}  {rat_s}")

        # ── PROPERTIES (sale) ─────────────────────────────────────────────────
        print()
        print(f"{'='*70}")
        print(" SALE PROPERTIES")
        print(f"{'='*70}")

        if total_sale == 0:
            print("  (empty)")
        else:
            props = (await s.execute(
                select(Property)
                .where(Property.price_thb.isnot(None))
                .order_by(Property.district, Property.price_thb)
                .limit(60)
            )).scalars().all()

            print(f"{'#':>4}  {'source':<13} {'district':<18} {'type':<12} {'BR':>3} {'sqm':>6}"
                  f"  {'price_thb':>12}  {'price/sqm':>9}  name")
            print("  " + "-" * 100)
            for i, p in enumerate(props, 1):
                br  = str(p.bedrooms) if p.bedrooms is not None else " -"
                sqm = f"{p.area_sqm:>6.0f}" if p.area_sqm else "     -"
                pr  = f"{p.price_thb:>12,.0f}" if p.price_thb else "           -"
                psm = f"{p.price_per_sqm_thb:>9,.0f}" if p.price_per_sqm_thb else "        -"
                nm  = (p.project_name or "")[:30]
                print(f"{i:>4}  {p.source:<13} {p.district:<18} {p.property_type or '':<12} {br:>3} {sqm}"
                      f"  {pr}  {psm}  {nm}")

asyncio.run(dump())
