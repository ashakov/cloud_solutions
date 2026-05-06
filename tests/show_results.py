"""Print rental DB stats after a scrape run."""
import sys, asyncio, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from db.database import AsyncSessionLocal
from db.models import RentalListing
from sqlalchemy import select, func


async def show():
    async with AsyncSessionLocal() as s:

        # Totals by source
        rows = await s.execute(
            select(RentalListing.source, func.count()).group_by(RentalListing.source)
        )
        print("=== Listings by source ===")
        for src, cnt in rows:
            print(f"  {src:<15} {cnt}")

        # By property type
        rows = await s.execute(
            select(RentalListing.property_type, func.count())
            .group_by(RentalListing.property_type)
        )
        print("\n=== By property type ===")
        for pt, cnt in rows:
            print(f"  {pt:<15} {cnt}")

        # By district
        rows = await s.execute(
            select(RentalListing.district, func.count())
            .group_by(RentalListing.district)
            .order_by(func.count().desc())
            .limit(12)
        )
        print("\n=== By district (top 12) ===")
        for d, cnt in rows:
            print(f"  {d:<20} {cnt}")

        # Airbnb: avg nightly rate by season
        print("\n=== Airbnb: avg nightly rate (THB) by season + type ===")
        print(f"  {'season':<12} {'type':<10} {'avg_night':>10}  {'n':>5}")
        print("  " + "-" * 42)
        for season, col in [
            ("peak",     RentalListing.daily_rate_peak_thb),
            ("high",     RentalListing.daily_rate_high_thb),
            ("shoulder", RentalListing.daily_rate_shoulder_thb),
            ("low",      RentalListing.daily_rate_low_thb),
        ]:
            rows = await s.execute(
                select(
                    RentalListing.property_type,
                    func.round(func.avg(col), 0),
                    func.count(),
                )
                .where(RentalListing.source == "airbnb", col.isnot(None))
                .group_by(RentalListing.property_type)
                .order_by(RentalListing.property_type)
            )
            for ptype, avg, cnt in rows:
                print(f"  {season:<12} {ptype:<10} {avg:>10,.0f}  {cnt:>5}")

        # FazWaz: avg monthly rate by type
        rows = await s.execute(
            select(
                RentalListing.property_type,
                func.round(func.avg(RentalListing.monthly_rate_high_season_thb), 0),
                func.count(),
            )
            .where(
                RentalListing.source == "fazwaz_rent",
                RentalListing.monthly_rate_high_season_thb.isnot(None),
            )
            .group_by(RentalListing.property_type)
            .order_by(func.avg(RentalListing.monthly_rate_high_season_thb).desc())
        )
        print("\n=== FazWaz: avg monthly rate (THB) by type ===")
        print(f"  {'type':<15} {'avg_month':>12}  {'n':>5}")
        print("  " + "-" * 36)
        for ptype, avg, cnt in rows:
            print(f"  {ptype:<15} {avg:>12,.0f}  {cnt:>5}")

        # Airbnb: top listings by peak nightly rate
        rows = await s.execute(
            select(RentalListing)
            .where(
                RentalListing.source == "airbnb",
                RentalListing.daily_rate_peak_thb.isnot(None),
            )
            .order_by(RentalListing.daily_rate_peak_thb.desc())
            .limit(10)
        )
        listings = rows.scalars().all()
        print("\n=== Top 10 Airbnb listings by peak nightly rate ===")
        print(f"  {'district':<18} {'type':<8} {'BR':>3}  {'peak':>8}  {'high':>8}  {'low':>8}  {'rating':>6}  name")
        print("  " + "-" * 90)
        for r in listings:
            tags = []
            if r.has_pool:
                tags.append("pool")
            if r.has_sea_view:
                tags.append("view")
            tag_str = "/".join(tags)
            rating_str = f"{r.platform_rating:.2f}" if r.platform_rating else "   -"
            br = str(r.bedrooms) if r.bedrooms is not None else "?"
            peak  = f"{r.daily_rate_peak_thb:>8,.0f}" if r.daily_rate_peak_thb else "       -"
            high  = f"{r.daily_rate_high_thb:>8,.0f}" if r.daily_rate_high_thb else "       -"
            low   = f"{r.daily_rate_low_thb:>8,.0f}" if r.daily_rate_low_thb else "       -"
            try:
                raw = json.loads(r.raw_data or "{}")
                name = (raw.get("name") or raw.get("title") or "")[:35]
            except Exception:
                name = ""
            print(f"  {r.district:<18} {r.property_type:<8} {br:>3}  {peak}  {high}  {low}  {rating_str:>6}  {name}  {tag_str}")

        # FazWaz: top monthly rentals
        rows = await s.execute(
            select(RentalListing)
            .where(
                RentalListing.source == "fazwaz_rent",
                RentalListing.monthly_rate_high_season_thb.isnot(None),
            )
            .order_by(RentalListing.monthly_rate_high_season_thb.desc())
            .limit(10)
        )
        listings = rows.scalars().all()
        print("\n=== Top 10 FazWaz monthly rentals ===")
        print(f"  {'district':<18} {'type':<8} {'BR':>3}  {'THB/month':>10}  name")
        print("  " + "-" * 70)
        for r in listings:
            br = str(r.bedrooms) if r.bedrooms is not None else "?"
            monthly = r.monthly_rate_high_season_thb
            try:
                raw = json.loads(r.raw_data or "{}")
                name = (raw.get("name") or raw.get("title") or "")[:38]
            except Exception:
                name = ""
            print(f"  {r.district:<18} {r.property_type:<8} {br:>3}  {monthly:>10,.0f}  {name}")


asyncio.run(show())
