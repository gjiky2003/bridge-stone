"""
BridgeStone Capital — Points Pricing & Collateral Analysis (pricing.py)

PointsCalculator
  - calc_upfront_points(loan_amount, points_pct) -> upfront fee in dollars
  - calc_daily_points(loan_amount, daily_rate, days) -> total accrued daily points
  - suggest_daily_rate(loan_amount, risk_tier) -> recommended daily rate by tier
  - calc_monthly_io_payment(loan_amount, annual_rate) -> monthly I/O payment
  - generate_term_sheet(deal_data, points_type) -> dict matching Mike Krumbein format

CollateralAnalyzer
  - verify_free_and_clear(property_address) -> bool (mock)
  - estimate_available_equity(property_address) -> float
  - cross_collateral_ltv(subject_value, collateral_value, loan_amount) -> combined LTV
"""

import hashlib
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tier → daily points rate (as a decimal, e.g. 0.00025 = 0.025%)
TIER_DAILY_RATES = {
    "A": 0.00025,   # 0.025% per day
    "B": 0.00028,   # 0.028% per day
    "C": 0.00031,   # 0.031% per day
    "D": 0.00035,   # 0.035% per day
    "R": 0.0,
}

# Tier → base upfront rate (% of loan as points)
TIER_UPFRONT_RATES = {
    "A": 2.0,
    "B": 2.5,
    "C": 3.0,
    "D": 3.5,
    "R": 0.0,
}


class PointsCalculator:
    """Calculate points pricing — both upfront (traditional) and daily (Richmond-style)."""

    @staticmethod
    def calc_upfront_points(loan_amount: float, points_pct: float) -> float:
        """Calculate total upfront origination points fee.

        Args:
            loan_amount: Total loan principal
            points_pct: Points as a percentage (e.g. 2.5 = 2.5 points)

        Returns:
            Dollar amount of the points fee
        """
        if loan_amount <= 0 or points_pct <= 0:
            return 0.0
        return round(loan_amount * (points_pct / 100.0), 2)

    @staticmethod
    def calc_daily_points(loan_amount: float, daily_rate: float, days: int) -> float:
        """Calculate total accrued daily points over N days.

        Daily points accrue on the outstanding balance each day.
        Unlike interest, these are NOT amortized — they are a separate fee.

        Args:
            loan_amount: Current outstanding principal
            daily_rate: Daily rate as decimal (e.g. 0.00025 = 0.025%)
            days: Number of days since origination

        Returns:
            Total daily points accrued in dollars
        """
        if loan_amount <= 0 or daily_rate <= 0 or days <= 0:
            return 0.0
        return round(loan_amount * daily_rate * days, 2)

    @staticmethod
    def suggest_daily_rate(loan_amount: float, risk_tier: str) -> float:
        """Suggest a daily points rate based on risk tier.

        Args:
            loan_amount: Loan principal (for context, may influence +/– on a tier)
            risk_tier: 'A', 'B', 'C', or 'D'

        Returns:
            Daily rate as decimal
        """
        tier = risk_tier.upper() if risk_tier else "C"
        base_rate = TIER_DAILY_RATES.get(tier, TIER_DAILY_RATES["C"])

        # Slight adjustment for very large loans (small discount)
        if loan_amount > 500_000:
            base_rate = max(0.00020, base_rate - 0.00002)
        elif loan_amount > 1_000_000:
            base_rate = max(0.00018, base_rate - 0.00003)

        return round(base_rate, 6)

    @staticmethod
    def calc_monthly_io_payment(loan_amount: float, annual_rate: float) -> float:
        """Calculate monthly interest-only payment.

        Args:
            loan_amount: Principal balance
            annual_rate: Annual interest rate as percentage (e.g. 10.5)

        Returns:
            Monthly I/O payment amount
        """
        if loan_amount <= 0 or annual_rate <= 0:
            return 0.0
        monthly_rate = (annual_rate / 100.0) / 12.0
        return round(loan_amount * monthly_rate, 2)

    @staticmethod
    def generate_term_sheet(
        deal_data: Dict[str, Any],
        points_type: str = "upfront",
    ) -> Dict[str, Any]:
        """Generate a complete term sheet in Mike Krumbein's format.

        Mike's format includes: Borrower, Property, Loan Amount, Rate, Points Type,
        Points Amount, Monthly Payment, Term, Prepayment Penalty, Closing Timeline,
        Required Docs, Collateral, and Notes.

        Args:
            deal_data: Dict with keys: borrower_name, property_address, loan_amount,
                       interest_rate, risk_tier, term_months, exit_strategy,
                       financing_type, collateral_value (optional), points_type
            points_type: 'upfront' or 'daily'

        Returns:
            Dict with structured term sheet fields
        """
        loan_amount = float(deal_data.get("loan_amount", 0) or 0)
        interest_rate = float(deal_data.get("interest_rate", 0) or 0)
        risk_tier = deal_data.get("risk_tier", "C")
        term_months = int(deal_data.get("term_months", 12) or 12)
        financing_type = deal_data.get("financing_type", "down_payment")

        # Points calculation
        if points_type == "daily":
            daily_rate = PointsCalculator.suggest_daily_rate(loan_amount, risk_tier)
            # Show 30-day estimate for term sheet
            points_30day = PointsCalculator.calc_daily_points(loan_amount, daily_rate, 30)
            points_180day = PointsCalculator.calc_daily_points(loan_amount, daily_rate, 180)
            points_amount = points_30day
            points_display = f"{daily_rate*100:.3f}% daily (~${points_30day:,.0f}/mo, ~${points_180day:,.0f}/6mo)"
        else:
            tier_points_pct = TIER_UPFRONT_RATES.get(risk_tier, 2.5)
            points_amount = PointsCalculator.calc_upfront_points(loan_amount, tier_points_pct)
            points_display = f"{tier_points_pct:.1f} pts upfront (${points_amount:,.0f})"

        # Monthly payment
        monthly_payment = PointsCalculator.calc_monthly_io_payment(loan_amount, interest_rate)

        # Required docs based on deal type
        required_docs = [
            "Purchase Contract / HUD-1",
            "Entity Docs (LLC operating agreement, EIN letter)",
            "Scope of Work & Contractor Bid",
            "Proof of Insurance (ACORD 25)",
            "Title Commitment",
            "2 Months Bank Statements",
            "Credit Authorization",
            "Borrower Questionnaire",
        ]

        if financing_type == "cross_collateral":
            required_docs.extend([
                "Collateral Property Deed",
                "Collateral Property Title Report",
                "Collateral Property Insurance",
            ])

        return {
            "borrower_name": deal_data.get("borrower_name", ""),
            "property_address": deal_data.get("property_address", ""),
            "loan_amount": loan_amount,
            "interest_rate": interest_rate,
            "points_type": points_type,
            "points_display": points_display,
            "points_amount": round(points_amount, 2),
            "monthly_payment": monthly_payment,
            "term_months": term_months,
            "exit_strategy": deal_data.get("exit_strategy", "sale"),
            "financing_type": financing_type,
            "collateral_value": deal_data.get("collateral_value", None),
            "prepayment_penalty": "None — no prepayment penalty",
            "closing_timeline": "3–4 business days from receipt of all documents",
            "required_docs": required_docs,
            "origination_fee": f"${points_amount:,.0f} ({points_type})",
            "rate_lock": "30 days",
            "extension_policy": "0.50% of loan amount per 30-day extension",
            "generated_date": date.today().isoformat(),
        }


class CollateralAnalyzer:
    """Analyze cross-collateral properties for equity, LTV, and free-and-clear status."""

    @staticmethod
    def verify_free_and_clear(property_address: str) -> bool:
        """Mock verification — checks if the address hash ends with an even number.

        In production, this would query title records, public registry, or MERS.

        Args:
            property_address: Full street address string

        Returns:
            True if property appears free and clear
        """
        if not property_address:
            return False
        hash_val = hashlib.md5(property_address.encode()).hexdigest()
        last_char = hash_val[-1]
        # Even hex digit → free and clear
        return int(last_char, 16) % 2 == 0

    @staticmethod
    def estimate_available_equity(property_address: str) -> float:
        """Estimate available equity in a cross-collateral property.

        Uses a deterministic hash to produce a plausible equity value.

        Args:
            property_address: Full street address

        Returns:
            Estimated equity in dollars (mock: $50k–$500k range)
        """
        if not property_address:
            return 0.0
        hash_val = hashlib.md5(property_address.encode()).hexdigest()
        # Use first 8 hex chars as a number between 0 and ~4B, then map to 50k–500k
        equity_num = int(hash_val[:8], 16) % 451_000
        return round(equity_num + 50_000, 2)

    @staticmethod
    def cross_collateral_ltv(
        subject_value: float,
        collateral_value: float,
        loan_amount: float,
    ) -> float:
        """Calculate combined LTV using both subject and collateral properties.

        The combined LTV uses total value (subject + collateral) as denominator.

        Args:
            subject_value: ARV or appraised value of the subject property
            collateral_value: Estimated value of the collateral property
            loan_amount: Requested loan amount

        Returns:
            Combined LTV as a decimal (e.g. 0.65 = 65%)
        """
        total_value = subject_value + collateral_value
        if total_value <= 0 or loan_amount <= 0:
            return 0.0
        return round(loan_amount / total_value, 4)

    @staticmethod
    def max_loan_with_collateral(
        subject_value: float,
        collateral_value: float,
        max_combined_ltv: float = 0.70,
    ) -> float:
        """Calculate maximum loan amount at a given combined LTV.

        Args:
            subject_value: ARV of subject property
            collateral_value: Value of collateral property
            max_combined_ltv: Maximum combined LTV allowed (default 70%)

        Returns:
            Maximum loan amount
        """
        total_value = subject_value + collateral_value
        return round(total_value * max_combined_ltv, 2)
