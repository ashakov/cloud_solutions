"""
Seeder — populates the DB with realistic synthetic Phuket property data.
Use this in sandboxed environments (no internet) to test the full pipeline.

Run:  python main.py seed
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta

from db.database import init_db, AsyncSessionLocal
from db.models import ScraperRun
from parsers.base import RawListing
from parsers.normalizer import upsert_listing, rebuild_benchmarks

logger = logging.getLogger(__name__)

random.seed(42)

# ── Realistic market data per district ──────────────────────────────────────
DISTRICT_PROFILES = {
    "bang-tao": {
        "condo": {"freehold": (85_000, 220_000), "leasehold": (60_000, 140_000)},
        "villa":  {"freehold": (120_000, 350_000), "leasehold": (90_000, 250_000)},
    },
    "kamala": {
        "condo": {"freehold": (90_000, 200_000), "leasehold": (65_000, 130_000)},
        "villa":  {"freehold": (110_000, 300_000), "leasehold": (85_000, 220_000)},
    },
    "surin": {
        "condo": {"freehold": (100_000, 250_000), "leasehold": (70_000, 160_000)},
        "villa":  {"freehold": (140_000, 400_000), "leasehold": (100_000, 280_000)},
    },
    "layan": {
        "condo": {"freehold": (95_000, 230_000), "leasehold": (68_000, 150_000)},
        "villa":  {"freehold": (130_000, 380_000), "leasehold": (95_000, 260_000)},
    },
    "rawai": {
        "condo": {"freehold": (55_000, 130_000), "leasehold": (40_000, 90_000)},
        "villa":  {"freehold": (70_000, 180_000), "leasehold": (50_000, 130_000)},
    },
    "nai-harn": {
        "condo": {"freehold": (60_000, 140_000), "leasehold": (45_000, 100_000)},
        "villa":  {"freehold": (80_000, 200_000), "leasehold": (60_000, 150_000)},
    },
    "patong": {
        "condo": {"freehold": (65_000, 160_000), "leasehold": (50_000, 120_000)},
        "villa":  {"freehold": (85_000, 210_000), "leasehold": (65_000, 160_000)},
    },
    "cherng-talay": {
        "condo": {"freehold": (80_000, 190_000), "leasehold": (58_000, 130_000)},
        "villa":  {"freehold": (110_000, 320_000), "leasehold": (80_000, 230_000)},
    },
}

PROJECTS = {
    "bang-tao": [
        "Laguna Shores", "Bangtao Beach Residences", "Botanica Luxury",
        "Ananda Laguna", "The Residence Bangtao", "Aqua Villas",
    ],
    "kamala": [
        "Kamala Beach Estate", "The Privilege", "Andara Resort",
        "Kamala Hills", "SeaRidge Kamala",
    ],
    "surin": [
        "Surin Sabai", "Twin Palms Residences", "The Surin Phuket",
        "Baan Surin View", "Sunrise Surin",
    ],
    "layan": [
        "Layan Gardens", "Anantara Layan", "Baan Layan",
        "The Layan Estate", "Ocean Palms Layan",
    ],
    "rawai": [
        "Rawai Palm Beach", "Baan Rawai", "Rawai Residence",
        "Southern Gate", "Nara Estate Rawai",
    ],
    "nai-harn": [
        "Nai Harn Beach Condo", "The Breeze", "Baan Nai Harn",
        "Phuket Riviera", "Rock Garden",
    ],
    "patong": [
        "Bayshore Patong", "Diamond Cliff", "Patong Bay Hill",
        "Sea Sun Sand", "Amari Phuket",
    ],
    "cherng-talay": [
        "Cherng Talay Villas", "Mono Loft", "The Palm",
        "Zenithy", "Garden Ville Cherng Talay",
    ],
}

SOURCES = ["fazwaz", "dotproperty"]

CONDO_SIZES = [30, 35, 40, 45, 50, 55, 60, 65, 75, 85, 100, 120]
VILLA_SIZES  = [150, 180, 200, 220, 250, 280, 300, 350, 400, 500]

CAM_FEES   = [60, 70, 80, 90, 100, 110, 120]
SINKING    = [500, 600, 700, 800, 1000]


def _make_listing(
    district: str,
    property_type: str,
    ownership: str,
    idx: int,
) -> RawListing:
    source = SOURCES[idx % len(SOURCES)]
    project = random.choice(PROJECTS.get(district, ["Phuket Property"]))
    source_id = f"{district}_{property_type}_{ownership}_{idx}"

    price_range = DISTRICT_PROFILES[district][property_type][ownership]
    price_per_sqm = random.uniform(*price_range)

    sizes = CONDO_SIZES if property_type == "condo" else VILLA_SIZES
    area_sqm = float(random.choice(sizes))
    price_thb = round(price_per_sqm * area_sqm / 1000) * 1000

    bedrooms = (
        random.choice([1, 1, 1, 2, 2, 3]) if property_type == "condo"
        else random.choice([2, 3, 3, 4, 5])
    )
    bathrooms = bedrooms

    leasehold_years = random.choice([30, 30, 30, 90]) if ownership == "leasehold" else None

    cam_fee = random.choice(CAM_FEES) if property_type == "condo" else None
    sinking = random.choice(SINKING) if property_type == "condo" else None

    # Some condos have rental program with (usually inflated) yield claim
    rental_prog = random.random() < 0.35
    rental_yield = round(random.uniform(5.0, 10.0), 1) if rental_prog else None

    # Hotel licence more common in Patong, rarer elsewhere
    hl_prob = 0.6 if district == "patong" else 0.25
    has_hl = random.random() < hl_prob if rental_prog else False

    scraped_delta = timedelta(hours=random.randint(0, 72))
    scraped_at = datetime.utcnow() - scraped_delta

    return RawListing(
        source=source,
        source_id=source_id,
        url=f"https://{source}.com/phuket/{district}/{source_id}",
        project_name=project,
        district=district,
        property_type=property_type,
        ownership_type=ownership,
        leasehold_years=leasehold_years,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        area_sqm=area_sqm,
        price_thb=float(price_thb),
        price_per_sqm_thb=round(price_per_sqm, 2),
        cam_fee_per_sqm=float(cam_fee) if cam_fee else None,
        sinking_fund_per_sqm=float(sinking) if sinking else None,
        rental_yield_claimed=rental_yield,
        rental_program=rental_prog,
        has_hotel_license=has_hl,
        scraped_at=scraped_at,
        raw_data={
            "source": source,
            "district": district,
            "project": project,
            "synthetic": True,
        },
    )


async def seed(listings_per_combo: int = 40) -> None:
    await init_db()

    total = 0
    async with AsyncSessionLocal() as session:
        run = ScraperRun(
            source="seeder",
            district="all",
            property_type="all",
            started_at=datetime.utcnow(),
            status="running",
        )
        session.add(run)
        await session.commit()

        idx = 0
        for district, types in DISTRICT_PROFILES.items():
            for prop_type, ownerships in types.items():
                for ownership in ownerships:
                    for i in range(listings_per_combo):
                        raw = _make_listing(district, prop_type, ownership, idx)
                        await upsert_listing(session, raw)
                        total += 1
                        idx += 1

                    await session.commit()
                    logger.info(
                        "Seeded %s / %s / %s", district, prop_type, ownership
                    )

        run.status = "done"
        run.records_fetched = total
        run.records_new = total
        run.completed_at = datetime.utcnow()
        await session.commit()

        logger.info("Seeding complete: %d listings", total)
        logger.info("Rebuilding benchmarks...")
        n = await rebuild_benchmarks(session)
        logger.info("Benchmarks: %d rows", n)
