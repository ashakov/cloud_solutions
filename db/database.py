from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text
from config import DATABASE_URL
from db.models import Base  # noqa: F401 — imports RentalListing, PropertyKPI too

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables if they do not exist, then run incremental column migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)
    async with AsyncSessionLocal() as session:
        await session.execute(text("PRAGMA journal_mode=WAL"))
        await session.execute(text("PRAGMA foreign_keys=ON"))
        await session.commit()


async def _migrate_columns(conn) -> None:
    """Add new columns to existing tables that predate them."""
    _new_cols = {
        "properties": [
            ("distance_to_beach_m", "INTEGER"),
        ],
        "district_benchmarks": [
            ("avg_daily_rate_high_thb",  "REAL"),
            ("avg_monthly_rate_thb",     "REAL"),
            ("avg_rental_yield_pct",     "REAL"),
            ("occupancy_rate_avg",       "REAL"),
            ("sample_count_rentals",     "INTEGER"),
        ],
    }
    for table, cols in _new_cols.items():
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        for col_name, col_type in cols:
            if col_name not in existing:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                )


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
