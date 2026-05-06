import json
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, Index,
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
    distance_to_beach_m = Column(Integer)

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

    # Rental info
    monthly_rent_thb = Column(Float)                  # actual current rent if listed/known
    rental_type = Column(String(20))                  # short_term | long_term | unknown
    rental_yield_claimed = Column(Float)              # % yield as stated by developer
    rental_program = Column(Boolean)                  # guaranteed rental program?
    rental_pool_split = Column(Float)                 # investor's share 0–1 (e.g. 0.7 = 70/30)
    has_hotel_license = Column(Boolean)

    # Property features (affect OPEX estimates)
    has_pool = Column(Boolean)
    has_garden = Column(Boolean)
    has_gym = Column(Boolean)
    parking_spaces = Column(Integer)

    # Status & timeline
    occupancy_status = Column(String(30))             # vacant | rented | owner_occupied
    is_off_plan = Column(Boolean)                     # under construction / pre-sale
    completion_date = Column(DateTime)                # expected handover (off-plan)
    year_built = Column(Integer)

    # Market liquidity signals
    days_on_market = Column(Integer)                  # how long listed (if available)
    price_drop_count = Column(Integer)                # number of price reductions
    zone_type = Column(String(30))                    # tourist | residential | mixed

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

    # Rental metrics (populated by rebuild_benchmarks when rental data is available)
    avg_daily_rate_high_thb = Column(Float)      # median high-season daily rate from comps
    avg_monthly_rate_thb = Column(Float)         # median monthly rate from long-term comps
    avg_rental_yield_pct = Column(Float)         # implied gross yield vs median sale price
    occupancy_rate_avg = Column(Float)           # base-scenario weighted avg occupancy
    sample_count_rentals = Column(Integer)       # rental comps used

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


# ── Rental layer ──────────────────────────────────────────────────────────────

class RentalListing(Base):
    """Rental listing scraped from Airbnb, Booking, Agoda, FazWaz-rent, etc."""
    __tablename__ = "rental_listings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)    # airbnb|booking|agoda|fazwaz_rent
    source_id = Column(String(300), nullable=False)
    url = Column(Text, nullable=False)

    # Location
    district = Column(String(100))
    subdistrict = Column(String(100))
    lat = Column(Float)
    lon = Column(Float)
    distance_to_beach_m = Column(Integer)          # metres; parsed from listing text

    # Property attributes
    property_type = Column(String(50))             # condo|villa|house
    bedrooms = Column(Integer)
    bathrooms = Column(Integer)
    area_sqm = Column(Float)
    max_guests = Column(Integer)

    # Amenities
    has_pool = Column(Boolean)
    has_gym = Column(Boolean)
    has_garden = Column(Boolean)
    has_parking = Column(Boolean)
    has_sea_view = Column(Boolean)
    has_management_company = Column(Boolean)
    management_company_name = Column(String(300))

    # ── Short-term nightly rates (THB) ───────────────────────────────────────
    # Seasons defined in core/seasonality.py — Phuket calendar
    daily_rate_peak_thb = Column(Float)       # Dec 20–Jan 10, Songkran Apr 10–16
    daily_rate_high_thb = Column(Float)       # Nov–Dec 19, Jan 11–Apr 9, Apr 17–30
    daily_rate_shoulder_thb = Column(Float)   # May, Oct
    daily_rate_low_thb = Column(Float)        # Jun–Sep (monsoon)

    # ── Long-term monthly rates (THB) ────────────────────────────────────────
    monthly_rate_high_season_thb = Column(Float)   # Nov–Apr
    monthly_rate_low_season_thb = Column(Float)    # May–Oct

    # Flat annual contract rate
    annual_rate_thb = Column(Float)

    # Terms
    min_stay_nights = Column(Integer)
    rental_type = Column(String(20))          # short_term|long_term|both

    # Demand signals
    claimed_occupancy_rate = Column(Float)    # 0.0–1.0; stated by owner/agent
    platform_reviews_count = Column(Integer)  # proxy for booking volume
    platform_rating = Column(Float)           # 1.0–5.0

    # Fees captured from listing
    management_fee_pct = Column(Float)        # 0.0–1.0 (e.g. 0.25 = 25/75 split)
    cleaning_fee_thb = Column(Float)

    raw_data = Column(Text)
    scraped_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_rental_source_listing"),
        Index("ix_rental_district", "district"),
        Index("ix_rental_type_beds", "property_type", "bedrooms"),
    )

    def set_raw(self, data: dict) -> None:
        self.raw_data = json.dumps(data, ensure_ascii=False, default=str)


class RentalCalendar(Base):
    """Sparse availability/price calendar scraped per rental listing.
    Only dates where data was actually retrieved are stored."""
    __tablename__ = "rental_calendar"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rental_listing_id = Column(Integer, ForeignKey("rental_listings.id", ondelete="CASCADE"),
                               nullable=False)
    date = Column(Date, nullable=False)
    is_available = Column(Boolean)
    rate_thb = Column(Float)
    season_tag = Column(String(20))           # peak|high|shoulder|low

    __table_args__ = (
        UniqueConstraint("rental_listing_id", "date", name="uq_calendar_listing_date"),
        Index("ix_calendar_date", "date"),
    )


class PropertyKPI(Base):
    """Calculated investment KPIs linking a sale listing to rental comps."""
    __tablename__ = "property_kpis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id", ondelete="CASCADE"),
                         nullable=False)
    rental_listing_id = Column(Integer, ForeignKey("rental_listings.id"), nullable=True)
    comp_method = Column(String(30))          # direct_link|district_median|manual

    # ── Investment inputs ────────────────────────────────────────────────────
    purchase_price_thb = Column(Float)
    transfer_fees_thb = Column(Float)         # ~2–6% of purchase price
    furniture_cost_thb = Column(Float)
    total_investment_thb = Column(Float)      # price + fees + furniture

    # ── Seasonal rates used in calculation ───────────────────────────────────
    daily_rate_peak_thb = Column(Float)
    daily_rate_high_thb = Column(Float)
    daily_rate_shoulder_thb = Column(Float)
    daily_rate_low_thb = Column(Float)
    occupancy_peak = Column(Float)
    occupancy_high = Column(Float)
    occupancy_shoulder = Column(Float)
    occupancy_low = Column(Float)

    # ── Revenue ─────────────────────────────────────────────────────────────
    gross_annual_revenue_thb = Column(Float)

    # ── OPEX (all annual, THB) ───────────────────────────────────────────────
    management_fee_thb = Column(Float)        # % of gross revenue
    cam_fee_annual_thb = Column(Float)        # CAM per sqm × sqm × 12
    sinking_fund_annual_thb = Column(Float)   # amortised one-time fund ÷ 40 yrs
    land_tax_annual_thb = Column(Float)       # LBT: 0.3% assessed value (commercial)
    income_tax_annual_thb = Column(Float)     # 15% withholding on net rental income
    insurance_annual_thb = Column(Float)
    maintenance_annual_thb = Column(Float)    # ~1% of furniture value p.a.
    total_opex_thb = Column(Float)

    # ── KPIs ─────────────────────────────────────────────────────────────────
    net_annual_revenue_thb = Column(Float)
    gross_yield_pct = Column(Float)
    net_yield_pct = Column(Float)
    breakeven_years = Column(Float)
    roi_5yr_pct = Column(Float)
    roi_10yr_pct = Column(Float)

    scenario = Column(String(20))             # conservative|base|optimistic
    calculated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_kpis_property_id", "property_id"),
        Index("ix_kpis_net_yield", "net_yield_pct"),
    )
