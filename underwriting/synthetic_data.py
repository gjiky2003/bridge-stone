"""
BridgeStone Capital — Synthetic Deal Generator (synthetic_data.py)

The ``SyntheticDealGenerator`` creates realistic, reproducible synthetic deal
data for testing the underwriting engine without live APIs or database access.

Features:
    - Deterministic generation via seed-based randomness
    - Bridge (fix-and-flip) and DSCR (rental) deal types
    - Multiple market profiles: ``'typical'``, ``'hot'``, ``'cold'``, ``'balanced'``
    - Portfolio generation with configurable mix ratio
    - Realistic distributions for prices, rehab costs, rents, borrower profiles

Usage::

    from underwriting.synthetic_data import SyntheticDealGenerator

    gen = SyntheticDealGenerator(seed=42)
    bridge_deal = gen.generate_bridge_deal(market="typical")
    dscr_deal = gen.generate_dscr_deal(market="hot")
    portfolio = gen.generate_portfolio(n=100, mix_ratio=0.6)
"""

import hashlib
import logging
import os
import random
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Ensure the project root is on the path so we can import config
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Market profiles
# ---------------------------------------------------------------------------

MARKET_PROFILES: Dict[str, Dict[str, Any]] = {
    "typical": {
        "monthly_appreciation": 0.004,
        "days_on_market": 30,
        "inventory_months": 4.0,
        "price_reduction_pct": 0.25,
        "msa_population": 800000,
        "rent_growth": 0.03,
        "vacancy_rate": 0.06,
        "unemployment_rate": 0.045,
    },
    "hot": {
        "monthly_appreciation": 0.008,
        "days_on_market": 10,
        "inventory_months": 1.5,
        "price_reduction_pct": 0.08,
        "msa_population": 2500000,
        "rent_growth": 0.06,
        "vacancy_rate": 0.02,
        "unemployment_rate": 0.025,
    },
    "cold": {
        "monthly_appreciation": -0.002,
        "days_on_market": 75,
        "inventory_months": 9.0,
        "price_reduction_pct": 0.40,
        "msa_population": 150000,
        "rent_growth": -0.01,
        "vacancy_rate": 0.14,
        "unemployment_rate": 0.08,
    },
    "balanced": {
        "monthly_appreciation": 0.005,
        "days_on_market": 22,
        "inventory_months": 3.5,
        "price_reduction_pct": 0.18,
        "msa_population": 1200000,
        "rent_growth": 0.04,
        "vacancy_rate": 0.05,
        "unemployment_rate": 0.04,
    },
}

# Realistic property characteristics distributions
_PROPERTY_TYPES = ["SFR", "SFR", "SFR", "SFR", "Townhouse", "Townhouse", "Condo", "Condo", "Multi-Family"]
_BED_DIST = [2, 3, 3, 3, 3, 4, 4, 5]  # weighted toward 3
_BATH_DIST = [1.0, 1.0, 1.5, 2.0, 2.0, 2.0, 2.5, 3.0]  # weighted toward 2

# States and metros for realistic city/state generation
_METROS = [
    ("Austin", "TX"), ("Houston", "TX"), ("Dallas", "TX"), ("San Antonio", "TX"),
    ("Phoenix", "AZ"), ("Tucson", "AZ"),
    ("Atlanta", "GA"), ("Savannah", "GA"),
    ("Charlotte", "NC"), ("Raleigh", "NC"),
    ("Nashville", "TN"), ("Memphis", "TN"),
    ("Orlando", "FL"), ("Tampa", "FL"), ("Jacksonville", "FL"), ("Miami", "FL"),
    ("Indianapolis", "IN"),
    ("Columbus", "OH"), ("Cleveland", "OH"), ("Cincinnati", "OH"),
    ("Kansas City", "MO"), ("St Louis", "MO"),
    ("Birmingham", "AL"),
    ("Greenville", "SC"),
    ("Denver", "CO"),
]

_STREET_NAMES = [
    "Oak", "Maple", "Elm", "Pine", "Cedar", "Birch", "Walnut", "Cherry",
    "Ash", "Hickory", "Magnolia", "Willow", "Spruce", "Laurel", "Sycamore",
    "Dogwood", "Hawthorn", "Juniper", "Locust", "Cottonwood",
]
_STREET_TYPES = ["St", "Ave", "Ln", "Dr", "Ct", "Way", "Cir", "Pl", "Blvd", "Trl"]

_REHAB_SCOPES = [
    "cosmetic update — paint, flooring, fixtures",
    "kitchen remodel — new cabinets, countertops, appliances",
    "bathroom remodel — tile, vanity, fixtures",
    "kitchen + bath update — mid-range finishes",
    "full interior renovation — kitchen, baths, flooring, paint",
    "light rehab — paint, carpet, minor repairs",
    "major rehab — systems, roof, kitchen, baths, flooring",
    "gut rehab — down to studs, all new systems",
]

_EXIT_STRATEGIES = ["sale", "sale", "sale", "sale", "refinance", "refinance", "rent"]


# ---------------------------------------------------------------------------
# SyntheticDealGenerator
# ---------------------------------------------------------------------------

class SyntheticDealGenerator:
    """Generate reproducible synthetic deals for underwriting engine testing.

    Parameters
    ----------
    seed : int
        Master seed for reproducibility.  Different seeds produce different
        but equally realistic deal sequences.
    """

    def __init__(self, seed: int = 42):
        self._seed = seed
        self._rng = random.Random(seed)
        self._counter = 0
        logger.info(
            "SyntheticDealGenerator initialised with seed=%d", seed
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_bridge_deal(
        self,
        market: str = "typical",
    ) -> Dict[str, Any]:
        """Generate one synthetic bridge (fix-and-flip) deal.

        Parameters
        ----------
        market : str
            One of ``'typical'``, ``'hot'``, ``'cold'``, ``'balanced'``.

        Returns
        -------
        dict
            Keys: ``property``, ``deal``, ``borrower``, ``market``.
        """
        market_data = MARKET_PROFILES.get(market, MARKET_PROFILES["typical"]).copy()

        # --- Generate property ---
        prop, rehab_scope = self._generate_bridge_property(market_data)

        # --- Generate deal structure ---
        deal = self._generate_bridge_deal_structure(prop, rehab_scope, market_data)

        # --- Generate borrower ---
        borrower = self._generate_borrower(experienced=True)

        # --- Address ---
        address = self._gen_address()

        result = {
            "property": {**prop, "address": address},
            "deal": {**deal, "rehab_scope": rehab_scope},
            "borrower": borrower,
            "market": market_data,
        }

        logger.info(
            "Generated bridge deal: %s | ARV=$%.0f | Purchase=$%.0f | Rehab=$%.0f | "
            "Borrower=%d flips, %d FICO",
            address,
            prop["arv_mid"],
            deal["purchase_price"],
            deal["rehab_budget"],
            borrower["completed_flips"],
            borrower["credit_score"],
        )
        return result

    def generate_dscr_deal(
        self,
        market: str = "typical",
    ) -> Dict[str, Any]:
        """Generate one synthetic DSCR (rental) deal.

        Parameters
        ----------
        market : str
            One of ``'typical'``, ``'hot'``, ``'cold'``, ``'balanced'``.

        Returns
        -------
        dict
            Keys: ``property``, ``rent``, ``borrower``, ``market``.
        """
        market_data = MARKET_PROFILES.get(market, MARKET_PROFILES["typical"]).copy()

        # --- Generate property ---
        prop = self._generate_dscr_property(market_data)

        # --- Generate rent data ---
        rent = self._generate_rent_data(prop, market_data)

        # --- Generate borrower ---
        borrower = self._generate_borrower(experienced=True, rentals=True)

        # --- Address ---
        address = self._gen_address()

        result = {
            "property": {**prop, "address": address},
            "rent": rent,
            "borrower": borrower,
            "market": market_data,
        }

        logger.info(
            "Generated DSCR deal: %s | Value=$%.0f | Rent=$%.0f/mo | "
            "Borrower=%d rentals, %d FICO",
            address,
            prop.get("arv_mid", prop.get("value", 0)),
            rent["market_rent_mid"],
            borrower["completed_rentals"],
            borrower["credit_score"],
        )
        return result

    def generate_portfolio(
        self,
        n: int = 100,
        mix_ratio: float = 0.6,
        market_distribution: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate a portfolio of *n* synthetic deals.

        Parameters
        ----------
        n : int
            Total number of deals to generate.
        mix_ratio : float
            Fraction of deals that are bridge loans (remainder are DSCR).
            Default 0.6 → 60% bridge, 40% DSCR.
        market_distribution : dict, optional
            Mapping of market profile name → probability.
            E.g. ``{"typical": 0.5, "hot": 0.2, "cold": 0.1, "balanced": 0.2}``.
            Defaults to mostly typical with some variation.

        Returns
        -------
        list[dict]
            List of deal dictionaries, each tagged with ``product_type`` and
            ``market_profile`` for filtering.
        """
        if market_distribution is None:
            market_distribution = {
                "typical": 0.45,
                "hot": 0.20,
                "cold": 0.10,
                "balanced": 0.25,
            }

        markets, probs = zip(*market_distribution.items())

        n_bridge = int(n * mix_ratio)
        n_dscr = n - n_bridge

        logger.info(
            "Generating portfolio: %d deals (%d bridge, %d DSCR) — seed=%d",
            n, n_bridge, n_dscr, self._seed,
        )

        deals = []

        for i in range(n_bridge):
            market = self._rng.choices(markets, weights=probs, k=1)[0]
            deal = self.generate_bridge_deal(market=market)
            deal["product_type"] = "bridge"
            deal["market_profile"] = market
            deals.append(deal)

        for i in range(n_dscr):
            market = self._rng.choices(markets, weights=probs, k=1)[0]
            deal = self.generate_dscr_deal(market=market)
            deal["product_type"] = "dscr"
            deal["market_profile"] = market
            deals.append(deal)

        # Shuffle for realistic order
        self._rng.shuffle(deals)

        logger.info(
            "Portfolio complete: %d deals generated", len(deals)
        )
        return deals

    # ------------------------------------------------------------------
    # Property generation
    # ------------------------------------------------------------------

    def _generate_bridge_property(
        self,
        market_data: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], str]:
        """Generate a bridge property (needs rehab) with ARV estimate.

        Returns (property_dict, rehab_scope_string).
        """
        ptype = self._rng.choice(_PROPERTY_TYPES[:4])  # mostly SFR for bridge
        beds = self._rng.choice(_BED_DIST)
        baths = self._rng.choice(_BATH_DIST)
        sqft = self._rng.randint(900, 2800)
        year_built = self._rng.randint(1940, 2005)

        # Generate ARV (after rehab, so higher price range)
        base_price = self._rng.uniform(100000, 700000)

        # Adjust for property characteristics
        sqft_factor = (sqft / 1600) ** 0.6
        beds_factor = 1.0 + (beds - 3) * 0.08
        age_factor = max(0.7, 1.0 - max(0, 2025 - year_built) * 0.002)
        market_factor = 1.0 + market_data.get("monthly_appreciation", 0.004) * 30

        arv_mid = base_price * sqft_factor * beds_factor * age_factor * market_factor
        arv_low = arv_mid * 0.88
        arv_high = arv_mid * 1.12

        # ARV confidence — better in hotter markets
        comps_count = self._rng.randint(5, 12)
        confidence = min(1.0, 0.4 + comps_count * 0.05)

        # Rehab scope
        rehab_scope = self._rng.choice(_REHAB_SCOPES)

        # Rehab complexity from scope
        if "gut" in rehab_scope.lower():
            rehab_complexity = 5
        elif "major" in rehab_scope.lower():
            rehab_complexity = 4
        elif "full" in rehab_scope.lower():
            rehab_complexity = 4
        elif "kitchen" in rehab_scope.lower() and "bath" in rehab_scope.lower():
            rehab_complexity = 3
        elif "kitchen" in rehab_scope.lower() or "bath" in rehab_scope.lower():
            rehab_complexity = 2
        else:
            rehab_complexity = 1

        return {
            "property_type": ptype,
            "sqft": sqft,
            "beds": beds,
            "baths": round(baths, 1),
            "year_built": year_built,
            "arv_mid": round(arv_mid, 2),
            "arv_low": round(arv_low, 2),
            "arv_high": round(arv_high, 2),
            "arv_confidence": round(confidence, 3),
            "occupancy": "investment",
        }, rehab_scope

    def _generate_dscr_property(
        self,
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate a DSCR rental property."""
        ptype = self._rng.choice(_PROPERTY_TYPES)
        beds = self._rng.choice(_BED_DIST)
        baths = self._rng.choice(_BATH_DIST)
        sqft = self._rng.randint(800, 2400)
        year_built = self._rng.randint(1950, 2020)

        # Value: DSCR properties are typically lower value
        base_price = self._rng.uniform(80000, 400000)
        sqft_factor = (sqft / 1400) ** 0.5
        beds_factor = 1.0 + (beds - 3) * 0.06
        age_factor = max(0.75, 1.0 - max(0, 2025 - year_built) * 0.003)
        market_factor = 1.0 + market_data.get("monthly_appreciation", 0.003) * 24
        value = base_price * sqft_factor * beds_factor * age_factor * market_factor

        # Loan amount: 65–80% LTV
        ltv = self._rng.uniform(0.60, 0.80)
        loan_amount = round(value * ltv, 2)

        return {
            "property_type": ptype,
            "sqft": sqft,
            "beds": beds,
            "baths": round(baths, 1),
            "year_built": year_built,
            "arv_mid": round(value, 2),
            "value": round(value, 2),
            "loan_amount": loan_amount,
            "purchase_price": round(value * self._rng.uniform(0.85, 1.05), 2),
            "occupancy": "investment",
        }

    # ------------------------------------------------------------------
    # Deal structure generation
    # ------------------------------------------------------------------

    def _generate_bridge_deal_structure(
        self,
        prop: Dict[str, Any],
        rehab_scope: str,
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate the deal-structure numbers for a bridge loan."""

        arv = prop["arv_mid"]

        # Rehab budget: 10–40% of ARV depending on complexity
        if "gut" in rehab_scope.lower():
            rehab_pct = self._rng.uniform(0.30, 0.40)
        elif "major" in rehab_scope.lower() or "full" in rehab_scope.lower():
            rehab_pct = self._rng.uniform(0.18, 0.30)
        elif "kitchen" in rehab_scope.lower() and "bath" in rehab_scope.lower():
            rehab_pct = self._rng.uniform(0.10, 0.22)
        else:
            rehab_pct = self._rng.uniform(0.05, 0.15)

        rehab_budget = round(arv * rehab_pct, 2)

        # Purchase price: so that purchase + rehab = 70–80% of ARV (profit margin)
        target_total = arv * self._rng.uniform(0.68, 0.80)
        purchase_price = max(0, round(target_total - rehab_budget, 2))

        # Loan amount
        total_cost = purchase_price + rehab_budget
        ltv = min(0.70, self._rng.uniform(0.60, 0.72))
        loan_amount = round(total_cost * ltv, 2)

        # Projected hold
        projected_hold = self._rng.randint(3, 12)

        # Exit strategy
        exit_strategy = self._rng.choice(_EXIT_STRATEGIES)

        # Projected profit
        projected_profit = round(arv - total_cost, 2)
        projected_roi = round(
            projected_profit / max(total_cost, 1) * 100, 1
        )

        return {
            "purchase_price": purchase_price,
            "rehab_budget": rehab_budget,
            "loan_amount": loan_amount,
            "total_cost": round(total_cost, 2),
            "rehab_complexity": self._rng.randint(1, 5),
            "rehab_scope": rehab_scope,
            "projected_hold_months": projected_hold,
            "exit_strategy": exit_strategy,
            "ltv_arv": round(loan_amount / max(arv, 1), 3),
            "ltv_total": round(loan_amount / max(total_cost, 1), 3),
            "projected_profit": projected_profit,
            "projected_roi": projected_roi,
        }

    def _generate_rent_data(
        self,
        prop: Dict[str, Any],
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate rent estimate and DSCR data for a DSCR deal."""

        value = prop.get("arv_mid", prop.get("value", 200000))
        loan_amount = prop.get("loan_amount", value * 0.75)
        beds = prop.get("beds", 3)
        sqft = prop.get("sqft", 1400)
        year_built = prop.get("year_built", 1990)

        # Market rent: based on value and market conditions
        # Gross yield: 6%–12% depending on market
        base_yield = 0.08
        market_rent_growth = market_data.get("rent_growth", 0.03)
        yield_adjust = 1.0 + market_rent_growth * 3
        gross_yield = base_yield * yield_adjust * self._rng.uniform(0.85, 1.20)

        monthly_rent = round(value * gross_yield / 12, 2)
        monthly_rent = max(800, monthly_rent)

        # DSCR: pick a realistic rate
        rate = self._rng.uniform(7.5, 10.0)

        # Calculate mortgage payment (30-year amortization)
        monthly_rate = (rate / 100) / 12
        n_payments = 30 * 12
        if monthly_rate > 0:
            p_and_i = (
                loan_amount
                * monthly_rate
                * (1 + monthly_rate) ** n_payments
                / ((1 + monthly_rate) ** n_payments - 1)
            )
        else:
            p_and_i = loan_amount / n_payments

        # Operating expenses
        vacancy_pct = market_data.get("vacancy_rate", 0.06) * self._rng.uniform(0.8, 1.3)
        management = monthly_rent * 0.08
        maintenance = monthly_rent * 0.10
        vacancy_allowance = monthly_rent * vacancy_pct
        taxes_insurance = (value * 0.012 + value * 0.0035) / 12  # ~1.55%/yr total

        total_monthly_obligations = p_and_i + taxes_insurance + maintenance

        effective_gross = monthly_rent - vacancy_allowance - management

        if total_monthly_obligations > 0:
            dscr = effective_gross / total_monthly_obligations
        else:
            dscr = 1.50

        monthly_cashflow = effective_gross - total_monthly_obligations

        return {
            "market_rent_mid": monthly_rent,
            "market_rent_low": round(monthly_rent * 0.85, 2),
            "market_rent_high": round(monthly_rent * 1.15, 2),
            "monthly_rent": monthly_rent,
            "rent_per_sqft": round(monthly_rent / max(sqft, 1), 2),
            "dscr": round(dscr, 3),
            "monthly_cashflow": round(monthly_cashflow, 2),
            "monthly_p_and_i": round(p_and_i, 2),
            "monthly_taxes_insurance": round(taxes_insurance, 2),
            "management_pct": 0.08,
            "maintenance_pct": 0.10,
            "vacancy_pct": round(vacancy_pct, 3),
        }

    # ------------------------------------------------------------------
    # Borrower generation
    # ------------------------------------------------------------------

    def _generate_borrower(
        self,
        experienced: bool = True,
        rentals: bool = False,
    ) -> Dict[str, Any]:
        """Generate a realistic borrower profile.

        When ``experienced`` is True the borrower has at least 2 flips;
        otherwise they may be brand new.  ``rentals`` biases toward
        more rental properties.
        """
        # Credit score: realistic distribution centred at ~700
        credit_score = self._rng.choice(
            [self._rng.randint(620, 659)] * 4
            + [self._rng.randint(660, 699)] * 6
            + [self._rng.randint(700, 739)] * 7
            + [self._rng.randint(740, 779)] * 5
            + [self._rng.randint(780, 850)] * 3
        )

        # Completed flips
        if experienced:
            completed_flips = self._rng.choice(
                [2] * 5 + [3] * 3 + [4, 5, 6] + [7, 8, 9, 10]
            )
        else:
            completed_flips = self._rng.randint(0, 3)

        # Completed rentals
        if rentals:
            completed_rentals = self._rng.choice(
                [1, 2, 2, 3, 3, 4, 5, 7, 10, 12]
            )
        else:
            completed_rentals = self._rng.randint(0, max(0, completed_flips // 2))

        # Years experience: roughly 1 year per 2 flips
        years_experience = round(
            completed_flips * self._rng.uniform(0.4, 0.9) + self._rng.uniform(0, 1.5), 1
        )

        # Active projects
        current_active = self._rng.randint(0, min(5, max(0, completed_flips // 2)))

        # Net worth and liquid assets
        total_flips = completed_flips + completed_rentals
        if total_flips >= 10:
            net_worth = self._rng.uniform(500000, 3000000)
            liquid = net_worth * self._rng.uniform(0.15, 0.40)
        elif total_flips >= 5:
            net_worth = self._rng.uniform(200000, 1000000)
            liquid = net_worth * self._rng.uniform(0.10, 0.35)
        elif total_flips >= 2:
            net_worth = self._rng.uniform(100000, 500000)
            liquid = net_worth * self._rng.uniform(0.05, 0.30)
        else:
            net_worth = self._rng.uniform(50000, 250000)
            liquid = net_worth * self._rng.uniform(0.03, 0.25)

        return {
            "completed_flips": completed_flips,
            "completed_rentals": completed_rentals,
            "years_experience": years_experience,
            "credit_score": credit_score,
            "current_active_projects": current_active,
            "net_worth": round(net_worth, 2),
            "liquid_assets": round(liquid, 2),
        }

    # ------------------------------------------------------------------
    # Address generation
    # ------------------------------------------------------------------

    def _gen_address(self) -> str:
        """Generate a random but realistic US street address."""
        number = self._rng.randint(100, 9999)
        street_name = self._rng.choice(_STREET_NAMES)
        street_type = self._rng.choice(_STREET_TYPES)
        city, state = self._rng.choice(_METROS)
        zip_code = self._rng.randint(10000, 99999)
        return f"{number} {street_name} {street_type}, {city}, {state} {zip_code:05d}"

    # ------------------------------------------------------------------
    # Utility: generate a deal that passes gating
    # ------------------------------------------------------------------

    def generate_good_bridge_deal(self, market: str = "typical") -> Dict[str, Any]:
        """Generate a bridge deal that is guaranteed to pass gating rules.

        Useful for testing the scoring path without dealing with rejects."""
        for _ in range(20):
            deal = self.generate_bridge_deal(market=market)
            p = deal["property"]
            d = deal["deal"]
            b = deal["borrower"]

            # Check gating criteria
            if b["completed_flips"] < 2:
                continue
            arv = p["arv_mid"]
            if arv < 75000:
                continue
            ltv = d["loan_amount"] / max(arv, 1)
            if ltv > 0.70:
                continue
            rehab_pct = d["rehab_budget"] / max(arv, 1)
            if rehab_pct > 0.40:
                continue

            logger.info("Generated good bridge deal on attempt %d", _ + 1)
            return deal

        # Force one that passes
        logger.warning("Could not randomly generate good bridge deal — forcing")
        deal = self.generate_bridge_deal(market="hot")
        deal["borrower"]["completed_flips"] = 5
        deal["deal"]["loan_amount"] = deal["property"]["arv_mid"] * 0.60
        deal["deal"]["rehab_budget"] = deal["property"]["arv_mid"] * 0.20
        return deal

    def generate_good_dscr_deal(self, market: str = "typical") -> Dict[str, Any]:
        """Generate a DSCR deal that is guaranteed to pass gating rules."""
        for _ in range(20):
            deal = self.generate_dscr_deal(market=market)
            p = deal["property"]
            r = deal["rent"]
            b = deal["borrower"]

            if b["completed_rentals"] < 1:
                continue
            loan_amt = p.get("loan_amount", 200000)
            if loan_amt < 75000 or loan_amt > 400000:
                continue
            if p.get("arv_mid", p.get("value", 200000)) < 50000:
                continue
            if r.get("dscr", 0) < 1.00:
                continue

            logger.info("Generated good DSCR deal on attempt %d", _ + 1)
            return deal

        logger.warning("Could not randomly generate good DSCR deal — forcing")
        deal = self.generate_dscr_deal(market="hot")
        deal["borrower"]["completed_rentals"] = 3
        deal["property"]["loan_amount"] = 150000
        deal["rent"]["dscr"] = 1.25
        deal["rent"]["monthly_cashflow"] = 300
        return deal
