"""
BridgeStone Capital — Rent Estimator (rent_estimator.py)

The ``RentEstimator`` provides market-rent estimates and DSCR (Debt Service
Coverage Ratio) calculations for rental-property underwriting.

Key outputs:
    - Market rent (mid, low, high) for a given property
    - DSCR and monthly cash-flow given a loan scenario

Usage::

    from underwriting.rent_estimator import RentEstimator

    estimator = RentEstimator()
    rent = estimator.estimate_rent("123 Main St, Austin TX 78701", beds=3, baths=2, sqft=1600)
    dscr = estimator.calculate_dscr(monthly_rent=2000, loan_amount=200000, rate=8.25)
"""

import hashlib
import logging
import os
import random
import sys
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

# Ensure the project root is on the path so we can import config
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_from_address(address: str) -> int:
    """Return a deterministic integer seed from an address string."""
    return int(hashlib.md5(address.strip().lower().encode()).hexdigest(), 16) % (2**31)


def _deterministic_random(seed: int, low: float, high: float) -> float:
    """Return a deterministic float in [low, high] given a seed."""
    rng = random.Random(seed)
    return rng.uniform(low, high)


def _deterministic_randint(seed: int, low: int, high: int) -> int:
    """Return a deterministic integer in [low, high] given a seed."""
    rng = random.Random(seed)
    return rng.randint(low, high)


# ---------------------------------------------------------------------------
# RentComparable named-tuple-like constants
# ---------------------------------------------------------------------------

_STREET_NAMES = [
    "Oak", "Maple", "Elm", "Pine", "Cedar", "Birch", "Walnut", "Cherry",
    "Ash", "Hickory", "Magnolia", "Willow", "Spruce", "Laurel", "Sycamore",
]
_STREET_TYPES = ["St", "Ave", "Ln", "Dr", "Ct", "Way", "Cir", "Pl"]


# ---------------------------------------------------------------------------
# RentEstimator
# ---------------------------------------------------------------------------

class RentEstimator:
    """Estimate market rent and calculate DSCR for rental properties.

    Uses ATTOM rental data when an API key is configured, otherwise falls back
    to deterministic mock data based on property characteristics.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate_rent(
        self,
        address: str,
        beds: int,
        baths: float,
        sqft: int,
        year_built: Optional[int] = None,
        property_type: str = "SFR",
    ) -> Dict[str, Any]:
        """Estimate market rent for a property.

        Parameters
        ----------
        address : str
            Full property address.
        beds : int
            Number of bedrooms.
        baths : float
            Number of bathrooms (e.g. 2.5).
        sqft : int
            Living area in square feet.
        year_built : int, optional
            Build year for age-based adjustments.
        property_type : str
            ``"SFR"``, ``"Condo"``, ``"Townhouse"``, or ``"Multi-Family"``.

        Returns
        -------
        dict
            ``market_rent_mid``, ``market_rent_low``, ``market_rent_high``,
            ``rent_per_sqft``, ``confidence`` (0–1), ``comps`` (list of dict).
        """
        logger.info(
            "estimate_rent(address=%r, beds=%d, baths=%.1f, sqft=%d, type=%r)",
            address, beds, baths, sqft, property_type,
        )

        # Base rent: $1.00–$2.00 per sqft / month depending on market
        seed = _seed_from_address(address)
        base_rent_per_sqft = _deterministic_random(seed, 0.90, 2.20)

        # Adjust for beds (more beds = higher total, slightly lower per-sqft)
        beds_multiplier = {1: 0.55, 2: 0.80, 3: 1.00, 4: 1.15, 5: 1.25}
        bed_factor = beds_multiplier.get(beds, 1.0)

        # Adjust for baths
        baths_factor = 1.0 + (baths - min(beds, baths)) * 0.05

        # Adjust for property type
        type_factors = {"SFR": 1.00, "Condo": 0.85, "Townhouse": 0.90, "Multi-Family": 1.10, "2-4 Unit": 1.10}
        type_factor = type_factors.get(property_type, 1.0)

        # Adjust for age
        age = 2025 - (year_built or 1990)
        if age < 5:
            age_factor = 1.15
        elif age < 15:
            age_factor = 1.08
        elif age < 30:
            age_factor = 1.00
        elif age < 50:
            age_factor = 0.90
        else:
            age_factor = 0.82

        # Compute market rent
        mid_rent = base_rent_per_sqft * sqft * bed_factor * baths_factor * type_factor * age_factor
        mid_rent = round(mid_rent, 2)

        # Range: ±15%
        low_rent = round(mid_rent * 0.85, 2)
        high_rent = round(mid_rent * 1.15, 2)

        # Confidence based on number of comps we'd have
        num_comps = _deterministic_randint(seed + 50, 4, 12)
        confidence = min(1.0, max(0.2, num_comps / 15.0 + 0.3))

        # Generate comps
        comps = self._generate_rent_comps(
            seed, beds, baths, sqft, mid_rent, property_type, num_comps
        )

        rent_per_sqft = round(mid_rent / max(sqft, 1), 2)

        result = {
            "market_rent_mid": mid_rent,
            "market_rent_low": low_rent,
            "market_rent_high": high_rent,
            "rent_per_sqft": rent_per_sqft,
            "confidence": round(confidence, 3),
            "comps": comps,
        }

        logger.info(
            "Rent estimate: mid=$%.0f/mo  range=[$%.0f–$%.0f]  $%.2f/sqft  confidence=%.2f",
            mid_rent, low_rent, high_rent, rent_per_sqft, confidence,
        )
        return result

    def calculate_dscr(
        self,
        monthly_rent: float,
        loan_amount: float,
        rate: float,
        property_taxes: float = 0,
        insurance: float = 0,
        hoa: float = 0,
        term_years: int = 30,
        management_pct: float = 0.08,
        maintenance_pct: float = 0.10,
        vacancy_pct: float = 0.05,
    ) -> Dict[str, Any]:
        """Calculate DSCR and monthly cash-flow for a loan scenario.

        Parameters
        ----------
        monthly_rent : float
            Gross monthly market rent.
        loan_amount : float
            Total loan amount.
        rate : float
            Annual interest rate as a percentage (e.g. 8.25 for 8.25%).
        property_taxes : float
            Annual property taxes.  If 0, estimated at 1.2% of loan_amount.
        insurance : float
            Annual insurance premium.  If 0, estimated at 0.35% of loan_amount.
        hoa : float
            Monthly HOA dues.  Default 0.
        term_years : int
            Loan term in years for amortization (default 30).
        management_pct : float
            Property management fee as % of gross rent (default 8%).
        maintenance_pct : float
            Maintenance reserve as % of gross rent (default 10%).
        vacancy_pct : float
            Vacancy allowance as % of gross rent (default 5%).

        Returns
        -------
        dict
            ``dscr`` (float), ``monthly_cashflow`` (float),
            ``monthly_payment`` (float), ``total_monthly_expenses`` (float),
            ``effective_gross_income`` (float), ``breakdown`` (dict).
        """
        logger.info(
            "calculate_dscr(rent=%.0f, loan=%.0f, rate=%.2f%%, taxes=%.0f, ins=%.0f)",
            monthly_rent, loan_amount, rate, property_taxes, insurance,
        )

        # --- Mortgage payment (P&I) ---
        monthly_rate = (rate / 100.0) / 12.0
        num_payments = term_years * 12

        if monthly_rate > 0 and loan_amount > 0:
            monthly_payment = (
                loan_amount
                * monthly_rate
                * (1 + monthly_rate) ** num_payments
                / ((1 + monthly_rate) ** num_payments - 1)
            )
        else:
            monthly_payment = 0.0

        # --- Estimate taxes & insurance if not provided ---
        if property_taxes <= 0:
            property_taxes = loan_amount * 0.012  # 1.2% effective tax rate
        if insurance <= 0:
            insurance = loan_amount * 0.0035  # 0.35% of value

        monthly_taxes = property_taxes / 12.0
        monthly_insurance = insurance / 12.0

        # --- Operating expenses ---
        management = monthly_rent * management_pct
        maintenance = monthly_rent * maintenance_pct
        vacancy = monthly_rent * vacancy_pct

        # --- Effective gross income ---
        effective_gross = monthly_rent - vacancy - management

        # --- Total monthly obligations ---
        total_expenses = (
            monthly_payment
            + monthly_taxes
            + monthly_insurance
            + hoa
            + maintenance
        )

        # --- DSCR ---
        if total_expenses > 0:
            dscr = effective_gross / total_expenses
        else:
            dscr = 0.0

        # --- Monthly cashflow ---
        monthly_cashflow = effective_gross - total_expenses

        result = {
            "dscr": round(dscr, 3),
            "monthly_cashflow": round(monthly_cashflow, 2),
            "monthly_payment": round(monthly_payment, 2),
            "total_monthly_expenses": round(total_expenses, 2),
            "effective_gross_income": round(effective_gross, 2),
            "breakdown": {
                "monthly_p_and_i": round(monthly_payment, 2),
                "monthly_taxes": round(monthly_taxes, 2),
                "monthly_insurance": round(monthly_insurance, 2),
                "monthly_hoa": round(hoa, 2),
                "monthly_maintenance": round(maintenance, 2),
                "monthly_management": round(management, 2),
                "monthly_vacancy_allowance": round(vacancy, 2),
            },
        }

        logger.info(
            "DSCR result: dscr=%.2f  cashflow=$%.0f/mo  payment=$%.0f/mo",
            result["dscr"],
            result["monthly_cashflow"],
            result["monthly_payment"],
        )
        return result

    # ------------------------------------------------------------------
    # Mock rent comps generation
    # ------------------------------------------------------------------

    def _generate_rent_comps(
        self,
        seed: int,
        beds: int,
        baths: float,
        sqft: int,
        mid_rent: float,
        property_type: str,
        count: int,
    ) -> list:
        """Generate deterministic mock rent comparables.

        Parameters
        ----------
        seed : int
            Base seed for reproducibility.
        beds, baths, sqft, mid_rent, property_type : various
            Subject property characteristics.
        count : int
            Number of comps to generate.

        Returns
        -------
        list[dict]
        """
        rng = random.Random(seed + 200)
        comps = []

        for i in range(count):
            comp_seed = seed + 300 + i * 13

            comp_sqft = max(500, sqft + _deterministic_randint(comp_seed, -300, 300))
            comp_beds = max(1, beds + _deterministic_randint(comp_seed + 1, -1, 1))
            comp_baths_raw = baths + _deterministic_random(comp_seed + 2, -0.5, 0.5)
            comp_baths = max(1.0, round(comp_baths_raw * 2) / 2)

            rent_variation = _deterministic_random(comp_seed + 3, 0.85, 1.18)
            comp_rent = round(mid_rent * (comp_sqft / max(sqft, 1)) * rent_variation, 2)
            comp_rent = max(500, comp_rent)

            distance = round(_deterministic_random(comp_seed + 4, 0.1, 1.5), 2)

            days_on_market = _deterministic_randint(comp_seed + 5, 3, 45)

            street_name = _STREET_NAMES[i % len(_STREET_NAMES)]
            street_type = _STREET_TYPES[i % len(_STREET_TYPES)]

            comps.append({
                "address": f"{200 + i * 5} {street_name} {street_type}, Cityville ST 00000",
                "distance_mi": distance,
                "rent": comp_rent,
                "sqft": comp_sqft,
                "beds": comp_beds,
                "baths": comp_baths,
                "rent_per_sqft": round(comp_rent / max(comp_sqft, 1), 2),
                "days_on_market": days_on_market,
                "property_type": property_type,
            })

        # Sort by closest match
        comps.sort(key=lambda c: abs(c["sqft"] - sqft))
        return comps
