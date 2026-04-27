"""
Investment KPI calculator for Phuket rental properties.

Thai tax assumptions (2024):
  Land & Building Tax   0.30 % of government-assessed value (commercial rental)
  Withholding income    15 % flat on net rental receipts (non-resident juristic)
  Transfer fee          2 %  of registered value (buyer's share under 50/50 split)
  Business tax (SBT)    3.3% in lieu of VAT — applies if sold within 5 years

OPEX defaults (override via KPIParams):
  CAM fee               60 THB/m²/month (market midpoint; range 30–120)
  Sinking fund          500 THB/m² amortised over 40 years → 12.5 THB/m²/yr
  Management fee        25 % of gross revenue (short-term; 10 % for long-term)
  Insurance             0.15% of property value/year
  Maintenance           1.0 % of furniture+fitout cost/year
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from core.seasonality import annual_revenue, SeasonBlock

Scenario = Literal["conservative", "base", "optimistic"]

# ── Tax & fee constants ───────────────────────────────────────────────────────

LAND_TAX_RATE = 0.003          # 0.30% of assessed value
INCOME_TAX_RATE = 0.15         # 15% withholding on net rental income
TRANSFER_FEE_RATE = 0.02       # 2% of registered price (buyer share)

DEFAULT_CAM_PER_SQM_MONTH = 60.0        # THB / m² / month
DEFAULT_SINKING_AMORT_YEARS = 40        # amortise one-time sinking fund over 40 yrs
DEFAULT_SINKING_PER_SQM = 500.0         # THB / m² one-time
DEFAULT_MANAGEMENT_FEE_SHORT = 0.25     # 25% of gross for short-term
DEFAULT_MANAGEMENT_FEE_LONG = 0.10      # 10% of gross for long-term
DEFAULT_INSURANCE_RATE = 0.0015         # 0.15% of purchase price / year
DEFAULT_MAINTENANCE_RATE = 0.01         # 1% of furniture cost / year


@dataclass
class KPIParams:
    """All inputs needed to compute a full investment KPI sheet."""

    # ── Purchase ─────────────────────────────────────────────────────────────
    purchase_price_thb: float
    area_sqm: float
    furniture_cost_thb: float = 0.0
    assessed_value_thb: float | None = None   # for LBT; defaults to purchase_price

    # ── Rental rates (short-term nightly, THB) ───────────────────────────────
    daily_peak_thb: float = 0.0
    daily_high_thb: float = 0.0
    daily_shoulder_thb: float = 0.0
    daily_low_thb: float = 0.0

    # ── Long-term monthly (THB) — used if daily rates not available ──────────
    monthly_high_thb: float = 0.0
    monthly_low_thb: float = 0.0

    # ── OPEX overrides ────────────────────────────────────────────────────────
    cam_per_sqm_month: float = DEFAULT_CAM_PER_SQM_MONTH
    sinking_fund_per_sqm: float = DEFAULT_SINKING_PER_SQM
    management_fee_pct: float | None = None   # None → use rental_type default
    rental_type: Literal["short_term", "long_term"] = "short_term"

    scenario: Scenario = "base"


@dataclass
class KPIResult:
    scenario: Scenario

    # Revenue
    gross_annual_revenue_thb: float
    season_blocks: list[SeasonBlock]

    # OPEX breakdown
    management_fee_thb: float
    cam_fee_annual_thb: float
    sinking_fund_annual_thb: float
    land_tax_annual_thb: float
    income_tax_annual_thb: float
    insurance_annual_thb: float
    maintenance_annual_thb: float
    total_opex_thb: float

    # Net
    net_annual_revenue_thb: float
    total_investment_thb: float

    # KPIs
    gross_yield_pct: float
    net_yield_pct: float
    breakeven_years: float
    roi_5yr_pct: float
    roi_10yr_pct: float

    def summary(self) -> str:
        lines = [
            f"  Scenario          : {self.scenario}",
            f"  Gross revenue/yr  : ฿{self.gross_annual_revenue_thb:>12,.0f}",
            f"  Total OPEX/yr     : ฿{self.total_opex_thb:>12,.0f}",
            f"    management fee  : ฿{self.management_fee_thb:>12,.0f}",
            f"    CAM             : ฿{self.cam_fee_annual_thb:>12,.0f}",
            f"    sinking fund    : ฿{self.sinking_fund_annual_thb:>12,.0f}",
            f"    land tax        : ฿{self.land_tax_annual_thb:>12,.0f}",
            f"    income tax      : ฿{self.income_tax_annual_thb:>12,.0f}",
            f"    insurance       : ฿{self.insurance_annual_thb:>12,.0f}",
            f"    maintenance     : ฿{self.maintenance_annual_thb:>12,.0f}",
            f"  Net revenue/yr    : ฿{self.net_annual_revenue_thb:>12,.0f}",
            f"  ────────────────────────────────────────",
            f"  Gross yield       : {self.gross_yield_pct:>7.2f}%",
            f"  Net yield         : {self.net_yield_pct:>7.2f}%",
            f"  Breakeven         : {self.breakeven_years:>7.1f} years",
            f"  ROI 5-year        : {self.roi_5yr_pct:>7.1f}%",
            f"  ROI 10-year       : {self.roi_10yr_pct:>7.1f}%",
        ]
        return "\n".join(lines)


def calculate_kpis(params: KPIParams) -> KPIResult:
    """
    Full investment KPI calculation.

    Short-term revenue formula:
        Gross = Σ_s ( days_s × occupancy_s × daily_rate_s )

    Long-term revenue formula (used when daily rates not supplied):
        Gross = monthly_high × 6 + monthly_low × 6

    Net yield formula:
        Net_Revenue = Gross - Management_Fee - CAM - Sinking - LBT - Income_Tax
                      - Insurance - Maintenance
        Net_Yield   = Net_Revenue / Total_Investment × 100

    Breakeven:
        Breakeven_Years = Total_Investment / Net_Revenue

    ROI (cumulative, simple — excludes capital appreciation):
        ROI_Nyrs = Net_Revenue × N / Total_Investment × 100
    """
    p = params

    # ── Total investment ─────────────────────────────────────────────────────
    transfer_fees = p.purchase_price_thb * TRANSFER_FEE_RATE
    total_investment = p.purchase_price_thb + transfer_fees + p.furniture_cost_thb

    # ── Gross annual revenue ─────────────────────────────────────────────────
    if p.daily_peak_thb and p.daily_high_thb:
        gross, blocks = annual_revenue(
            p.daily_peak_thb, p.daily_high_thb,
            p.daily_shoulder_thb, p.daily_low_thb,
            scenario=p.scenario,
        )
    elif p.monthly_high_thb:
        # Long-term: 6 high-season months + 6 low-season months
        gross = p.monthly_high_thb * 6 + (p.monthly_low_thb or p.monthly_high_thb * 0.7) * 6
        blocks = []
    else:
        raise ValueError("Provide either daily nightly rates or monthly long-term rates.")

    # ── OPEX ─────────────────────────────────────────────────────────────────
    mgmt_rate = p.management_fee_pct
    if mgmt_rate is None:
        mgmt_rate = (DEFAULT_MANAGEMENT_FEE_SHORT if p.rental_type == "short_term"
                     else DEFAULT_MANAGEMENT_FEE_LONG)
    management_fee = gross * mgmt_rate

    cam_annual = p.cam_per_sqm_month * p.area_sqm * 12

    sinking_annual = (p.sinking_fund_per_sqm * p.area_sqm) / DEFAULT_SINKING_AMORT_YEARS

    assessed = p.assessed_value_thb or p.purchase_price_thb
    land_tax = assessed * LAND_TAX_RATE

    # Income tax: 15% on net income after management fee (most common structure)
    taxable_income = gross - management_fee
    income_tax = taxable_income * INCOME_TAX_RATE

    insurance = p.purchase_price_thb * DEFAULT_INSURANCE_RATE

    maintenance = p.furniture_cost_thb * DEFAULT_MAINTENANCE_RATE

    total_opex = (management_fee + cam_annual + sinking_annual +
                  land_tax + income_tax + insurance + maintenance)

    # ── Net revenue & KPIs ───────────────────────────────────────────────────
    net_revenue = gross - total_opex

    gross_yield = (gross / total_investment) * 100 if total_investment else 0.0
    net_yield = (net_revenue / total_investment) * 100 if total_investment else 0.0
    breakeven = (total_investment / net_revenue) if net_revenue > 0 else math.inf
    roi_5yr = (net_revenue * 5 / total_investment) * 100 if total_investment else 0.0
    roi_10yr = (net_revenue * 10 / total_investment) * 100 if total_investment else 0.0

    return KPIResult(
        scenario=p.scenario,
        gross_annual_revenue_thb=round(gross, 2),
        season_blocks=blocks,
        management_fee_thb=round(management_fee, 2),
        cam_fee_annual_thb=round(cam_annual, 2),
        sinking_fund_annual_thb=round(sinking_annual, 2),
        land_tax_annual_thb=round(land_tax, 2),
        income_tax_annual_thb=round(income_tax, 2),
        insurance_annual_thb=round(insurance, 2),
        maintenance_annual_thb=round(maintenance, 2),
        total_opex_thb=round(total_opex, 2),
        net_annual_revenue_thb=round(net_revenue, 2),
        total_investment_thb=round(total_investment, 2),
        gross_yield_pct=round(gross_yield, 4),
        net_yield_pct=round(net_yield, 4),
        breakeven_years=round(breakeven, 2),
        roi_5yr_pct=round(roi_5yr, 4),
        roi_10yr_pct=round(roi_10yr, 4),
    )


def all_scenarios(params: KPIParams) -> dict[Scenario, KPIResult]:
    """Run conservative / base / optimistic in one call."""
    results = {}
    for sc in ("conservative", "base", "optimistic"):
        p = KPIParams(**{**params.__dict__, "scenario": sc})
        results[sc] = calculate_kpis(p)
    return results


# ── Comps engine ──────────────────────────────────────────────────────────────

@dataclass
class CompResult:
    """Output of a single comparable property analysis."""
    district: str
    property_type: str
    bedrooms: int | None

    sale_price_thb: float
    comp_daily_high_thb: float        # median of comparable rentals
    comp_sample_count: int

    implied_gross_yield_pct: float    # if bought at sale_price and rented at comp rates
    district_median_yield_pct: float  # benchmark from DB
    undervaluation_score: float       # >0 = underpriced vs peers (higher is better)


def score_comps(
    sale_price: float,
    area_sqm: float,
    comp_rentals: list[dict],         # list of {"daily_high_thb": x, "bedrooms": n, ...}
    district_median_yield: float,
    scenario: Scenario = "base",
) -> CompResult | None:
    """
    Compare a for-sale property against rental comps in the same district.

    Undervaluation score:
        score = implied_yield / district_median_yield - 1
        > 0  → property generates more yield than market median at ask price (buy signal)
        < 0  → priced above what rental income justifies

    Usage in agent report:
        "This 2BR condo in Bang Tao asks ฿5.5M. Comparable 2BR rentals yield
         ฿3,200/night in high season. At that rate, gross yield = 9.1% vs
         district median 6.3% → undervaluation score +0.44 (strong buy)."
    """
    if not comp_rentals:
        return None

    daily_highs = [r["daily_high_thb"] for r in comp_rentals if r.get("daily_high_thb")]
    if not daily_highs:
        return None

    median_daily_high = sorted(daily_highs)[len(daily_highs) // 2]

    # Estimate other season rates from high-season rate using market multipliers
    from core.seasonality import implied_daily_rates
    rates = implied_daily_rates(
        monthly_high=median_daily_high * 30,
        monthly_low=median_daily_high * 30 * 0.65,
    )

    kpi = calculate_kpis(KPIParams(
        purchase_price_thb=sale_price,
        area_sqm=area_sqm,
        daily_peak_thb=rates["peak"],
        daily_high_thb=rates["high"],
        daily_shoulder_thb=rates["shoulder"],
        daily_low_thb=rates["low"],
        scenario=scenario,
    ))

    bedrooms = comp_rentals[0].get("bedrooms")
    undervaluation = (kpi.gross_yield_pct / district_median_yield - 1
                      if district_median_yield else 0.0)

    return CompResult(
        district=comp_rentals[0].get("district", ""),
        property_type=comp_rentals[0].get("property_type", ""),
        bedrooms=bedrooms,
        sale_price_thb=sale_price,
        comp_daily_high_thb=median_daily_high,
        comp_sample_count=len(daily_highs),
        implied_gross_yield_pct=round(kpi.gross_yield_pct, 4),
        district_median_yield_pct=round(district_median_yield, 4),
        undervaluation_score=round(undervaluation, 4),
    )
