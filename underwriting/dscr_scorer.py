"""
BridgeStone Capital — DSCR Loan Scorer (dscr_scorer.py)

The ``DSCRScorer`` evaluates rental-property loans using a cash-flow-centred
4-pillar scoring framework:

    ===============  ======
    Pillar           Weight
    ===============  ======
    Cash Flow         40 %
    Property          30 %
    Market            20 %
    Borrower          10 %
    ===============  ======

Every DSCR loan goes through two stages:

**Stage 1 — Gating Rules**
    Mandatory checks.  Any failure returns tier ``'R'`` (Reject) with score 0.

**Stage 2 — Weighted Scoring**
    Each pillar is scored 0–100 and combined by weight to produce an overall
    score that maps to a tier:

    =========  ===========  ============
    Score      Tier          Max LTV
    =========  ===========  ============
    85–100     A (Prime)     80 %
    70–84      B (Good)      75 %
    55–69      C (Fair)      70 %
    40–54      D (Marginal)  65 %
     0–39      R (Reject)     —
    =========  ===========  ============

Usage::

    from underwriting.dscr_scorer import DSCRScorer

    scorer = DSCRScorer()
    result = scorer.score_loan(property_data, rent_data, borrower_data, market_data)
    # result is {"score": 78, "tier": "B", "dscr": 1.22, "max_ltv": 0.75, ...}
"""

import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure the project root is on the path so we can import config
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIER_MAP: Dict[Tuple[int, int], str] = {
    (85, 100): "A",
    (70, 84): "B",
    (55, 69): "C",
    (40, 54): "D",
    (0, 39): "R",
}

# Base rates and points by tier — aligned with LendingOne/FOA industry data
_BASE_RATES = {"A": 6.75, "B": 7.50, "C": 8.25, "D": 9.50, "R": 0.0}
_BASE_POINTS = {"A": 1.5, "B": 2.0, "C": 2.5, "D": 3.0, "R": 0.0}

# DSCR → LTV cascade (industry standard: FOA + LendingOne)
# DSCR ≥ 1.25 → 80% LTV (best)
# DSCR 1.15-1.24 → 80% LTV (good)
# DSCR 1.00-1.14 → 75% LTV (FOA: "capped at 75% due to DSCR being below 1.00")
# DSCR 0.75-0.99 → 70% LTV (LendingOne RentalFlex, higher rate)
# DSCR < 0.75 → Reject
DSCR_LTV_MAP = [
    (1.25, 0.80),   # Excellent cash flow
    (1.15, 0.80),   # Strong cash flow
    (1.00, 0.75),   # Adequate — FOA policy
    (0.75, 0.70),   # RentalFlex tier — higher rate
]

# Gating rule constants
MIN_DSCR = 0.75           # RentalFlex minimum (LendingOne: 0.75)
MIN_DSCR_STANDARD = 1.00  # Standard minimum
MAX_LTV = 0.80
MIN_RENTAL_PROPERTIES = 1
MIN_LOAN_AMOUNT = 75000
MAX_LOAN_AMOUNT = 400000

# Rent discount for DSCR calculation — industry standard 25%
# LendingOne: "Do you discount the estimated rent before DSCR?"
# Standard: 75% of gross rent used (25% haircut for vacancy + maintenance + management)
RENT_DISCOUNT_FACTOR = 0.75

# Vacant property reserve — 3 months PITI (FOA policy)
VACANT_RESERVE_MONTHS = 3


# ---------------------------------------------------------------------------
# DSCRScorer
# ---------------------------------------------------------------------------

class DSCRScorer:
    """Score DSCR (Debt Service Coverage Ratio) loans for rental properties.

    Uses a rules-based weighted scoring framework heavily biased toward
    cash-flow strength (40 %).
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_loan(
        self,
        property_data: Dict[str, Any],
        rent_data: Dict[str, Any],
        borrower_data: Dict[str, Any],
        market_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Score a DSCR loan and return the full underwriting decision.

        Parameters
        ----------
        property_data : dict
            Must contain: ``arv_mid`` (or ``value``), ``property_type``,
            ``sqft``, ``beds``, ``baths``, ``year_built``.
            May contain: ``loan_amount``, ``purchase_price``.
        rent_data : dict
            Must contain: ``market_rent_mid`` (or ``monthly_rent``).
            May contain: ``dscr``, ``monthly_cashflow``, ``expenses``.
        borrower_data : dict
            Must contain: ``completed_rentals``, ``credit_score``.
            May contain: ``completed_flips``, ``years_experience``,
            ``net_worth``, ``liquid_assets``.
        market_data : dict, optional
            May contain: ``monthly_appreciation``, ``rent_growth``,
            ``vacancy_rate``, ``msa_population``.

        Returns
        -------
        dict
            ``score`` (int), ``tier`` (str), ``dscr`` (float),
            ``max_ltv`` (float), ``rate`` (float), ``points`` (float),
            ``monthly_cashflow`` (float), ``flags`` (list[str]),
            and pillar scores.
        """
        logger.info(
            "score_loan — value=$%.0f, rent=$%.0f/mo, rentals=%d, FICO=%d",
            property_data.get("arv_mid", property_data.get("value", 0)),
            rent_data.get("market_rent_mid", rent_data.get("monthly_rent", 0)),
            borrower_data.get("completed_rentals", 0),
            borrower_data.get("credit_score", 0),
        )

        if market_data is None:
            market_data = {}

        flags: List[str] = []
        loan_amount = property_data.get(
            "loan_amount",
            property_data.get("purchase_price", 200000),
        )

        # ==================================================================
        # Stage 1 — Gating Rules
        # ==================================================================
        gating = self._run_gating_rules(property_data, rent_data, borrower_data)
        if not gating["passed"]:
            logger.warning("DSCR gating FAILED: %s", gating["reasons"])
            return {
                "score": 0,
                "tier": "R",
                "dscr": 0.0,
                "max_ltv": 0.0,
                "rate": 0.0,
                "points": 0.0,
                "monthly_cashflow": 0.0,
                "flags": gating["reasons"],
                "pillar_scores": {},
                "cashflow_score": 0,
                "property_score": 0,
                "market_score": 0,
                "borrower_score": 0,
            }

        # ==================================================================
        # Stage 2 — Weighted Scoring
        # ==================================================================

        # Pillar 1: Cash Flow (40%)
        cashflow_score, dscr, monthly_cf = self._score_cashflow(
            property_data, rent_data, flags
        )

        # Pillar 2: Property (30%)
        property_score = self._score_property(property_data, rent_data, flags)

        # Pillar 3: Market (20%)
        market_score = self._score_market(market_data, rent_data, flags)

        # Pillar 4: Borrower (10%)
        borrower_score = self._score_borrower(borrower_data, flags)

        pillar_scores = {
            "cashflow": cashflow_score,
            "property": property_score,
            "market": market_score,
            "borrower": borrower_score,
        }

        # Weighted total
        weights = {
            "cashflow": 0.40,
            "property": 0.30,
            "market": 0.20,
            "borrower": 0.10,
        }
        total_score = sum(pillar_scores[k] * weights[k] for k in weights)
        total_score = max(0, min(100, total_score))
        score = round(total_score)

        # Map to tier
        tier = self._score_to_tier(score)

        # Determine max LTV, rate, points
        max_ltv = self._compute_max_ltv(tier, score)
        rate = self._compute_rate(tier, score)
        points = self._compute_points(tier, score)

        result = {
            "score": score,
            "tier": tier,
            "dscr": round(dscr, 3),
            "max_ltv": max_ltv,
            "rate": rate,
            "points": points,
            "monthly_cashflow": round(monthly_cf, 2),
            "flags": flags,
            "pillar_scores": pillar_scores,
            "cashflow_score": cashflow_score,
            "property_score": property_score,
            "market_score": market_score,
            "borrower_score": borrower_score,
        }

        logger.info(
            "score_loan result: SCORE=%d TIER=%s DSCR=%.2f max_ltv=%.0f%% "
            "rate=%.2f%% points=%.1f cashflow=$%.0f",
            score,
            tier,
            dscr,
            max_ltv * 100,
            rate,
            points,
            monthly_cf,
        )
        return result

    # ------------------------------------------------------------------
    # Stage 1 — Gating Rules
    # ------------------------------------------------------------------

    def _run_gating_rules(
        self,
        property_data: Dict[str, Any],
        rent_data: Dict[str, Any],
        borrower_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check all mandatory DSCR gating rules.

        Uses cascading DSCR→LTV map instead of flat thresholds.
        Matches FOA/LendingOne industry standards.
        """
        
        reasons: List[str] = []
        
        # 1. DSCR gating — use cascading map
        dscr = rent_data.get("dscr", 0)
        ltv_cap = MAX_LTV  # default
        if dscr > 0:
            ltv_cap = 0.0
            for threshold, cap in DSCR_LTV_MAP:
                if dscr >= threshold:
                    ltv_cap = cap
                    break
            if dscr < MIN_DSCR:
                reasons.append(
                    f"DSCR ({dscr:.2f}) below minimum ({MIN_DSCR:.2f})"
                )
        
        # 2. Max LTV — calculated from DSCR cascade
        value = property_data.get("arv_mid", property_data.get("value", 0))
        loan_amount = property_data.get(
            "loan_amount",
            property_data.get("purchase_price", 0),
        )
        if value > 0 and loan_amount > 0:
            ltv = loan_amount / value
            if ltv > ltv_cap:
                reasons.append(
                    f"LTV ({ltv:.1%}) exceeds DSCR-based cap ({ltv_cap:.0%} for DSCR {dscr:.2f})"
                )
        
        # 3. Minimum 1 rental property experience
        rentals = borrower_data.get("completed_rentals", 0)
        if rentals < MIN_RENTAL_PROPERTIES:
            reasons.append(
                f"Rental property count ({rentals}) below minimum ({MIN_RENTAL_PROPERTIES})"
            )
        
        # 4. Min loan amount
        if loan_amount > 0 and loan_amount < MIN_LOAN_AMOUNT:
            reasons.append(
                f"Loan amount (${loan_amount:,.0f}) below minimum (${MIN_LOAN_AMOUNT:,.0f})"
            )
        
        # 5. Max loan amount
        if loan_amount > MAX_LOAN_AMOUNT:
            reasons.append(
                f"Loan amount (${loan_amount:,.0f}) exceeds maximum (${MAX_LOAN_AMOUNT:,.0f})"
            )
        
        # 6. NOT owner-occupied
        occupancy = property_data.get("occupancy", "investment")
        if occupancy == "owner_occupied":
            reasons.append("Owner-occupied properties not eligible for DSCR loans")
        
        return {"passed": len(reasons) == 0, "reasons": reasons, "ltv_cap": ltv_cap}

    # ------------------------------------------------------------------
    # Stage 2 — Pillar Scoring
    # ------------------------------------------------------------------

    def _score_cashflow(
        self,
        property_data: Dict[str, Any],
        rent_data: Dict[str, Any],
        flags: List[str],
    ) -> Tuple[float, float, float]:
        """Score the cash-flow pillar (0–100).  Returns (score, dscr, monthly_cf).

        Sub-components:
          - DSCR ratio: higher → safer (0–40)
          - Monthly cashflow: absolute dollars (0–25)
          - Rent-to-value ratio (GRM): higher yield → better (0–20)
          - Expense ratio: sensible expenses = sustainable (0–15)
        """
        score = 0.0

        dscr = rent_data.get("dscr", 1.20)
        monthly_cf = rent_data.get("monthly_cashflow", 200)
        monthly_rent = rent_data.get(
            "market_rent_mid", rent_data.get("monthly_rent", 1500)
        )
        value = property_data.get("arv_mid", property_data.get("value", 200000))
        expenses = rent_data.get("expenses", 0)

        # --- DSCR ratio (0–40) ---
        if dscr >= 1.50:
            score += 40
        elif dscr >= 1.35:
            score += 37
        elif dscr >= 1.25:
            score += 33
        elif dscr >= 1.15:
            score += 27
        elif dscr >= 1.05:
            score += 20
        elif dscr >= 1.00:
            score += 12
        else:
            score += 3
            flags.append(f"DSCR below 1.00: {dscr:.2f}")

        # --- Monthly cashflow (0–25) ---
        if monthly_cf >= 500:
            score += 25
        elif monthly_cf >= 350:
            score += 22
        elif monthly_cf >= 200:
            score += 18
        elif monthly_cf >= 100:
            score += 12
        elif monthly_cf > 0:
            score += 6
        else:
            score += 2
            flags.append(f"Negative monthly cashflow: ${monthly_cf:.0f}")

        # --- Rent-to-value ratio (GRM proxy) (0–20) ---
        if value > 0:
            monthly_yield = (monthly_rent * 12) / value
            if monthly_yield >= 0.12:  # 12% gross yield
                score += 20
            elif monthly_yield >= 0.10:
                score += 18
            elif monthly_yield >= 0.08:
                score += 15
            elif monthly_yield >= 0.06:
                score += 10
            elif monthly_yield >= 0.04:
                score += 5
            else:
                score += 2
                flags.append(f"Low gross yield: {monthly_yield:.1%}")

        # --- Expense ratio (0–15) ---
        if expenses <= 0 and monthly_rent > 0:
            # Estimate expense ratio: typical is 35-50% of rent
            expense_ratio = 0.40  # neutral
        elif monthly_rent > 0:
            expense_ratio = expenses / (monthly_rent * 12)

        # We score lower expenses as better (but not suspiciously low)
        if 0.25 <= expenses <= 0 or (0.30 <= (expense_ratio) <= 0.45):
            score += 15
        elif expense_ratio < 0.25:
            score += 10
            flags.append("Expense ratio unusually low — verify")
        elif expense_ratio <= 0.50:
            score += 12
        else:
            score += 6
            flags.append(f"High expense ratio: {expense_ratio:.0%}")

        return min(100, score), dscr, monthly_cf

    def _score_property(
        self,
        property_data: Dict[str, Any],
        rent_data: Optional[Dict[str, Any]] = None,
        flags: Optional[List[str]] = None,
    ) -> float:
        """Score the property pillar (0–100).

        Sub-components:
          - Property type: SFR and small multi-family preferred (0–20)
          - Age/condition (0–20)
          - Beds/baths: 3/2 is a renter sweet-spot (0–20)
          - Size: 1000-1800 sqft ideal for rentals (0–15)
          - LTV: lower is safer (0–15)
          - Rent/sqft efficiency (0–10)
        """
        if flags is None:
            flags = []

        score = 0.0

        # --- Property type (0–20) ---
        ptype = property_data.get("property_type", "SFR").upper()
        type_scores = {"SFR": 20, "TOWNHOUSE": 16, "CONDO": 14, "MULTI-FAMILY": 18, "2-4 UNIT": 18}
        score += type_scores.get(ptype, 12)

        # --- Year built / age (0–20) ---
        year = property_data.get("year_built", 1985)
        age = max(0, 2025 - year)
        if age < 5:
            score += 20
        elif age < 15:
            score += 18
        elif age < 30:
            score += 15
        elif age < 50:
            score += 10
        elif age < 70:
            score += 6
        else:
            score += 3
            flags.append(f"Older property: {age} years — maintenance risk")

        # --- Beds / Baths sweet spot for rentals (0–20) ---
        beds = property_data.get("beds", 3)
        baths = property_data.get("baths", 2.0)
        if beds == 3 and 1.5 <= baths <= 2.5:
            score += 20  # ideal
        elif beds == 3:
            score += 18
        elif beds == 2:
            score += 16
        elif beds == 4:
            score += 15
        else:
            score += 10

        # --- Size range (0–15) ---
        sqft = property_data.get("sqft", 1400)
        if 1000 <= sqft <= 1800:
            score += 15  # sweet spot for rentals
        elif 800 <= sqft <= 2200:
            score += 12
        else:
            score += 8

        # --- LTV (0–15) ---
        value = property_data.get("arv_mid", property_data.get("value", 200000))
        loan_amount = property_data.get("loan_amount", property_data.get("purchase_price", 160000))
        if value > 0:
            ltv = loan_amount / value
            if ltv <= 0.60:
                score += 15
            elif ltv <= 0.70:
                score += 13
            elif ltv <= 0.75:
                score += 10
            elif ltv <= 0.80:
                score += 6
            else:
                score += 3

        # --- Rent per sqft efficiency (0–10) ---
        monthly_rent = (rent_data or {}).get(
            "market_rent_mid",
            (rent_data or {}).get("monthly_rent", 1500),
        )
        if sqft > 0:
            rent_per_sqft = monthly_rent / sqft
            if rent_per_sqft >= 1.50:
                score += 10
            elif rent_per_sqft >= 1.20:
                score += 8
            elif rent_per_sqft >= 1.00:
                score += 6
            elif rent_per_sqft >= 0.80:
                score += 4
            else:
                score += 2

        return min(100, score)

    def _score_market(
        self,
        market_data: Dict[str, Any],
        rent_data: Optional[Dict[str, Any]] = None,
        flags: Optional[List[str]] = None,
    ) -> float:
        """Score the market pillar (0–100).

        Sub-components:
          - Rent growth trend (0–30)
          - Vacancy rate (0–25)
          - Property appreciation (0–20)
          - Population / demand (0–15)
          - Job market proxy (0–10)
        """
        if flags is None:
            flags = []

        score = 0.0

        # --- Rent growth (0–30) ---
        rent_growth = market_data.get("rent_growth", 0.03)  # 3% annual
        if rent_growth >= 0.06:
            score += 30
        elif rent_growth >= 0.04:
            score += 27
        elif rent_growth >= 0.03:
            score += 22
        elif rent_growth >= 0.01:
            score += 15
        elif rent_growth >= 0:
            score += 8
        else:
            score += 3
            flags.append(f"Declining rents: {rent_growth:.1%} growth rate")

        # --- Vacancy rate (0–25) ---
        vacancy = market_data.get("vacancy_rate", 0.06)
        if vacancy <= 0.03:
            score += 25
        elif vacancy <= 0.05:
            score += 22
        elif vacancy <= 0.07:
            score += 18
        elif vacancy <= 0.10:
            score += 12
        elif vacancy <= 0.15:
            score += 6
        else:
            score += 2
            flags.append(f"High vacancy: {vacancy:.0%}")

        # --- Property appreciation (0–20) ---
        appr = market_data.get("monthly_appreciation", 0.003)
        annual_appr = appr * 12
        if annual_appr >= 0.06:
            score += 20
        elif annual_appr >= 0.04:
            score += 17
        elif annual_appr >= 0.02:
            score += 13
        elif annual_appr >= 0.0:
            score += 8
        else:
            score += 3

        # --- Population / demand (0–15) ---
        pop = market_data.get("msa_population", 500000)
        if pop >= 2000000:
            score += 15
        elif pop >= 500000:
            score += 12
        elif pop >= 200000:
            score += 8
        elif pop >= 100000:
            score += 5
        else:
            score += 3

        # --- Job market proxy (0–10) ---
        unemployment = market_data.get("unemployment_rate", 0.045)
        if unemployment <= 0.03:
            score += 10
        elif unemployment <= 0.05:
            score += 8
        elif unemployment <= 0.07:
            score += 5
        else:
            score += 2
            flags.append(f"High unemployment: {unemployment:.0%}")

        return min(100, score)

    def _score_borrower(
        self,
        borrower_data: Dict[str, Any],
        flags: List[str],
    ) -> float:
        """Score the borrower pillar (0–100).

        Sub-components:
          - Rental experience: number of properties owned (0–35)
          - Credit score (0–30)
          - Landlord experience in years (0–20)
          - Financial reserves / liquidity (0–15)
        """
        score = 0.0

        # --- Rental properties owned (0–35) ---
        rentals = borrower_data.get("completed_rentals", 2)
        if rentals >= 10:
            score += 35
        elif rentals >= 5:
            score += 30
        elif rentals >= 3:
            score += 25
        elif rentals >= 2:
            score += 18
        else:
            score += 8

        # --- Credit score (0–30) ---
        fico = borrower_data.get("credit_score", 700)
        if fico >= 760:
            score += 30
        elif fico >= 720:
            score += 27
        elif fico >= 680:
            score += 22
        elif fico >= 650:
            score += 16
        elif fico >= 620:
            score += 10
        else:
            score += 4
            flags.append(f"Low credit score: {fico}")

        # --- Years of landlord experience (0–20) ---
        years = borrower_data.get("years_experience", 1)
        if years >= 10:
            score += 20
        elif years >= 5:
            score += 17
        elif years >= 3:
            score += 13
        elif years >= 2:
            score += 9
        else:
            score += 4
            flags.append(f"Limited rental experience: {years:.1f} yrs")

        # --- Financial reserves (0–15) ---
        liquid = borrower_data.get("liquid_assets", 50000)
        net_worth = borrower_data.get("net_worth", 150000)

        # Reserves for vacancy/maintenance
        if liquid >= 50000:
            score += 15
        elif liquid >= 25000:
            score += 12
        elif liquid >= 10000:
            score += 7
        else:
            score += 3
            flags.append(f"Low reserves: ${liquid:,.0f} liquid")

        # Extra boost for high net worth
        if net_worth >= 500000:
            pass  # already captured above

        return min(100, score)

    # ------------------------------------------------------------------
    # Tier, Rate, Points mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _score_to_tier(score: int) -> str:
        """Map a numeric score to its tier letter."""
        for (low, high), tier in TIER_MAP.items():
            if low <= score <= high:
                return tier
        return "R"

    @staticmethod
    def _compute_max_ltv(tier: str, score: int) -> float:
        """Determine maximum LTV for the given tier and score."""
        base_ltv = {
            "A": 0.80,
            "B": 0.75,
            "C": 0.70,
            "D": 0.65,
            "R": 0.0,
        }
        return base_ltv.get(tier, 0.0)

    @staticmethod
    def _compute_rate(tier: str, score: int) -> float:
        """Compute the interest rate for the tier and score.

        Higher scores within a tier get a slight rate reduction (±0.50%).
        """
        base = _BASE_RATES.get(tier, 0.0)
        if tier == "R":
            return 0.0
        for (low, high), _tier in TIER_MAP.items():
            if _tier == tier:
                range_span = high - low
                if range_span > 0:
                    position = (score - low) / range_span
                    rate = base - (position - 0.5) * 0.50
                    return round(max(base - 0.50, min(base + 0.50, rate)), 2)
        return round(base, 2)

    @staticmethod
    def _compute_points(tier: str, score: int) -> float:
        """Compute origination points for the tier and score."""
        base = _BASE_POINTS.get(tier, 0.0)
        if tier == "R":
            return 0.0
        for (low, high), _tier in TIER_MAP.items():
            if _tier == tier:
                range_span = high - low
                if range_span > 0:
                    position = (score - low) / range_span
                    pts = base - (position - 0.5) * 1.0
                    return round(max(base - 1.0, min(base + 1.0, pts)), 1)
        return round(base, 1)
