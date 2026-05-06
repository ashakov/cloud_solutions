"""
Phuket seasonality calendar.

Season definitions (confirmed market practice):
  Peak     — Dec 20–Jan 10, Songkran Apr 10–16   (~29 days/yr)
  High     — Nov 1–Dec 19, Jan 11–Apr 9, Apr 17–30  (~152 days/yr)
  Shoulder — May, October                            (~62 days/yr)
  Low      — Jun–Sep (SW monsoon)                    (~122 days/yr)

Occupancy benchmarks (mid-market Phuket managed rental, 2024–25):
  Peak      92%   (well-run villa/condo)
  High      80%
  Shoulder  55%
  Low       35%
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal

Season = Literal["peak", "high", "shoulder", "low"]

# (month, day_start, month, day_end) inclusive ranges → season
# Evaluated in order; first match wins.
_SEASON_RANGES: list[tuple[tuple[int, int], tuple[int, int], Season]] = [
    # Peak windows
    ((12, 20), (12, 31), "peak"),
    ((1,   1), (1,  10), "peak"),
    ((4,  10), (4,  16), "peak"),   # Songkran
    # High season (dry)
    ((11,  1), (12, 19), "high"),
    ((1,  11), (4,   9), "high"),
    ((4,  17), (4,  30), "high"),
    # Shoulder
    ((5,   1), (5,  31), "shoulder"),
    ((10,  1), (10, 31), "shoulder"),
    # Low / monsoon — everything else
]

# How many days of each season in a standard (non-leap) year
SEASON_DAYS: dict[Season, int] = {
    "peak":     29,
    "high":     152,
    "shoulder": 62,
    "low":      122,
}

# Market occupancy rates per season (base / conservative / optimistic)
OCCUPANCY: dict[str, dict[Season, float]] = {
    "base":         {"peak": 0.92, "high": 0.80, "shoulder": 0.55, "low": 0.35},
    "optimistic":   {"peak": 0.96, "high": 0.88, "shoulder": 0.68, "low": 0.48},
    "conservative": {"peak": 0.82, "high": 0.68, "shoulder": 0.40, "low": 0.22},
}


def classify_date(date: datetime.date) -> Season:
    """Return the season tag for a given calendar date."""
    for (m1, d1), (m2, d2), season in _SEASON_RANGES:
        start = datetime.date(date.year, m1, d1)
        # Handle year-end wrap (Dec 20–Dec 31)
        end_year = date.year if m2 >= m1 else date.year + 1
        end = datetime.date(end_year, m2, d2)
        if start <= date <= end:
            return season
    return "low"


@dataclass(frozen=True)
class SeasonBlock:
    season: Season
    days: int
    occupancy: float
    daily_rate_thb: float

    @property
    def revenue_thb(self) -> float:
        return self.days * self.occupancy * self.daily_rate_thb


def annual_revenue(
    daily_peak: float,
    daily_high: float,
    daily_shoulder: float,
    daily_low: float,
    scenario: str = "base",
) -> tuple[float, list[SeasonBlock]]:
    """
    Calculate gross annual short-term rental revenue using Phuket season weights.

    Returns (total_revenue_thb, [SeasonBlock, ...]) so callers can inspect
    the breakdown per season.

    Formula:
        Revenue = Σ (days_s × occupancy_s × daily_rate_s)   for s in seasons
    """
    occ = OCCUPANCY[scenario]
    rates: dict[Season, float] = {
        "peak":     daily_peak,
        "high":     daily_high,
        "shoulder": daily_shoulder,
        "low":      daily_low,
    }
    blocks = [
        SeasonBlock(
            season=s,
            days=SEASON_DAYS[s],
            occupancy=occ[s],
            daily_rate_thb=rates[s],
        )
        for s in ("peak", "high", "shoulder", "low")
    ]
    total = sum(b.revenue_thb for b in blocks)
    return total, blocks


def implied_daily_rates(
    monthly_high: float,
    monthly_low: float,
    *,
    peak_premium: float = 1.55,
    shoulder_discount: float = 0.75,
) -> dict[Season, float]:
    """
    Derive nightly rates from long-term monthly rates when short-term data
    is unavailable.

        High-season daily  ≈ monthly_high / 30
        Peak daily         ≈ high_daily × peak_premium   (default 1.55×)
        Shoulder daily     ≈ high_daily × shoulder_discount
        Low daily          ≈ monthly_low / 30

    Premiums are calibrated from Phuket market surveys (2024).
    """
    high_daily = monthly_high / 30
    return {
        "peak":     round(high_daily * peak_premium, 0),
        "high":     round(high_daily, 0),
        "shoulder": round(high_daily * shoulder_discount, 0),
        "low":      round(monthly_low / 30, 0),
    }
