import json
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer,
    String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Property(Base):
    """Individual property listing scraped from aggregators."""
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)       # fazwaz | dotproperty
    source_id = Column(String(300), nullable=False)   # unique id on the source site
    url = Column(Text, nullable=False)

    project_name = Column(String(500))
    developer = Column(String(500))
    unit_number = Column(String(100))

    district = Column(String(100))                    # canonical district name
    subdistrict = Column(String(100))
    lat = Column(Float)
    lon = Column(Float)

    property_type = Column(String(50))                # condo|villa|house|townhouse|land
    ownership_type = Column(String(50))               # freehold|leasehold|unknown
    leasehold_years = Column(Integer)
    title_deed = Column(String(100))                  # chanote|nor_sor_3|etc

    bedrooms = Column(Integer)
    bathrooms = Column(Integer)
    area_sqm = Column(Float)
    land_sqm = Column(Float)
    floor = Column(Integer)
    total_floors = Column(Integer)

    price_thb = Column(Float)
    price_per_sqm_thb = Column(Float)

    # Costs & fees (THB)
    cam_fee_per_sqm = Column(Float)                   # Common Area Maintenance per m²/month
    sinking_fund_per_sqm = Column(Float)              # one-time contribution per m²
    furniture_package_thb = Column(Float)

    # Rental info (from developer marketing)
    rental_yield_claimed = Column(Float)              # % as stated by developer
    rental_program = Column(Boolean)                  # guaranteed rental program?
    has_hotel_license = Column(Boolean)

    # Raw JSON blob — preserve everything scraped
    raw_data = Column(Text)

    scraped_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_source_listing"),
        Index("ix_properties_district", "district"),
        Index("ix_properties_property_type", "property_type"),
        Index("ix_properties_price_sqm", "price_per_sqm_thb"),
        Index("ix_properties_scraped_at", "scraped_at"),
    )

    def set_raw(self, data: dict) -> None:
        self.raw_data = json.dumps(data, ensure_ascii=False, default=str)

    def get_raw(self) -> dict:
        return json.loads(self.raw_data) if self.raw_data else {}

    def __repr__(self) -> str:
        return f"<Property {self.source}:{self.source_id} {self.district} {self.price_thb}THB>"


class DistrictBenchmark(Base):
    """Aggregated price benchmarks per district, recalculated after each scraper run."""
    __tablename__ = "district_benchmarks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    district = Column(String(100), nullable=False)
    property_type = Column(String(50), nullable=False)
    ownership_type = Column(String(50), nullable=False)  # freehold|leasehold|all

    median_price_per_sqm = Column(Float)
    avg_price_per_sqm = Column(Float)
    p25_price_per_sqm = Column(Float)
    p75_price_per_sqm = Column(Float)
    min_price_per_sqm = Column(Float)
    max_price_per_sqm = Column(Float)

    median_price_total = Column(Float)
    avg_price_total = Column(Float)

    sample_count = Column(Integer)
    calculated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_benchmarks_district_type", "district", "property_type"),
    )

    def __repr__(self) -> str:
        return (
            f"<Benchmark {self.district}/{self.property_type} "
            f"median={self.median_price_per_sqm:.0f} n={self.sample_count}>"
        )


class ScraperRun(Base):
    """Audit log for every scraper execution."""
    __tablename__ = "scraper_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)
    district = Column(String(100))
    property_type = Column(String(50))

    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    records_fetched = Column(Integer, default=0)
    records_new = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    status = Column(String(20), default="running")  # running|done|error
    error_message = Column(Text)

    def __repr__(self) -> str:
        return f"<ScraperRun {self.source} {self.status} fetched={self.records_fetched}>"
