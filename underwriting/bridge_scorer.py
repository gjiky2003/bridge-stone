"""
BridgeStone Capital — Hard Money / Bridge Loan Scorer (bridge_scorer.py)

The ``HardMoneyScorer`` evaluates fix-and-flip bridge loan deals using a
weighted 4-pillar scoring framework:

    ===============  ======
    Pillar           Weight
    ===============  ======
    Property          35 %
    Market            25 %
    Deal Structure    20 %
    Borrower          20 %
    ===============  ======

Every deal goes through two stages:

**Stage 1 — Gating Rules**
    Mandatory checks.  Any failure returns tier ``'R'`` (Reject) with score 0.

**Stage 2 — Weighted Scoring**
    Each pillar is scored 0–100 with sub-components, then combined by weight
    into an overall score mapped to a tier:

    =========  ===========  ============
    Score      Tier          Max LTV ARV
    =========  ===========  ============
    85–100     A (Prime)     70 %
    70–84      B (Good)      70 %
    55–69      C (Fair)      65 %
    40–54      D (Marginal)  60 %
     0–39      R (Reject)     —
    =========  ===========  ============

Usage::

    from underwriting.bridge_scorer import HardMoneyScorer

    scorer = HardMoneyScorer()
    result = scorer.score_deal(property_data, deal_data, borrower_data, market_data)
    # result is {"score": 72, "tier": "B", "max_ltv": 0.70, "rate": 10.5, ...}
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

# Rate and points by tier (base, adjusts for fine score)
_BASE_RATES = {"A": 9.75, "B": 10.50, "C": 11.25, "D": 12.00, "R": 0.0}
_BASE_POINTS = {"A": 2.0, "B": 2.5, "C": 3.0, "D": 3.5, "R": 0.0}

# Gating rule constants
MIN_COMPLETED_FLIPS = 2
MAX_LTV_ARV = 0.70
MIN_BORROWER_EQUITY = 0.10  # 10% of total project cost
MAX_REHAB_PCT_OF_ARV = 0.40
MIN_ARV = 75000.0


# ---------------------------------------------------------------------------
# HardMoneyScorer
# ---------------------------------------------------------------------------

class HardMoneyScorer:
    """Score bridge (hard-money) loans for fix-and-flip and rehab projects.

    This is a rules-based scoring engine that mimics the behaviour of an ML
    model (e.g. XGBoost) by combining weighted sub-scores from four pillars.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_deal(
        self,
        property_data: Dict[str, Any],
        deal_data: Dict[str, Any],
        borrower_data: Dict[str, Any],
        market_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Score a bridge loan deal and return the full decision payload.

        Parameters
        ----------
        property_data : dict
            Must contain at least: ``arv_mid``, ``property_type``, ``sqft``,
            ``beds``, ``baths``, ``year_built``.
            May also contain ``arv_low``, ``arv_high``, ``arv_confidence``.
        deal_data : dict
            Must contain at least: ``purchase_price``, ``rehab_budget``.
            May also contain ``loan_amount``, ``rehab_complexity`` (1-5),
            ``rehab_scope``, ``exit_strategy``, ``projected_hold_months``.
        borrower_data : dict
            Must contain at least: ``completed_flips``, ``years_experience``,
            ``credit_score``.
            May also contain ``completed_rentals``, ``current_active_projects``,
            ``net_worth``, ``liquid_assets``.
        market_data : dict, optional
            May contain: ``monthly_appreciation``, ``days_on_market``,
            ``inventory_months``, ``price_reduction_pct``, ``msa_population``.
            Defaults to a neutral midpoint.

        Returns
        -------
        dict
            ``score`` (int 0–100), ``tier`` (str A/B/C/D/R),
            ``max_ltv`` (float), ``rate`` (float), ``points`` (float),
            ``flags`` (list[str]), and pillar-level breakdowns.
        """
        logger.info(
            "score_deal — property=$%.0f ARV, purchase=$%.0f, rehab=$%.0f, "
            "borrower_experience=%.1f yrs, %d flips, FICO=%d",
            property_data.get("arv_mid", 0),
            deal_data.get("purchase_price", 0),
            deal_data.get("rehab_budget", 0),
            borrower_data.get("years_experience", 0),
            borrower_data.get("completed_flips", 0),
            borrower_data.get("credit_score", 0),
        )

        if market_data is None:
            market_data = {}

        flags: List[str] = []
        pillar_scores: Dict[str, float] = {}

        # ==================================================================
        # Stage 1 — Gating Rules
        # ==================================================================
        gating = self._run_gating_rules(property_data, deal_data, borrower_data)
        if not gating["passed"]:
            logger.warning("Gating FAILED: %s", gating["reasons"])
            result = {
                "score": 0,
                "tier": "R",
                "max_ltv": 0.0,
                "rate": 0.0,
                "points": 0.0,
                "flags": gating["reasons"],
                "pillar_scores": {},
                "property_score": 0,
                "market_score": 0,
                "deal_score": 0,
                "borrower_score": 0,
            }
            logger.info("score_deal result: TIER=R (gating failure)")
            return result

        # ==================================================================
        # Stage 2 — Weighted Scoring
        # ==================================================================

        # Pillar 1: Property (35%)
        property_score = self._score_property(property_data, flags)

        # Pillar 2: Market (25%)
        market_score = self._score_market(market_data, property_data, flags)

        # Pillar 3: Deal Structure (20%)
        deal_score_raw = self._score_deal_structure(property_data, deal_data, flags)

        # Pillar 4: Borrower (20%)
        borrower_score = self._score_borrower(borrower_data, flags)

        pillar_scores = {
            "property": property_score,
            "market": market_score,
            "deal": deal_score_raw,
            "borrower": borrower_score,
        }

        # Weighted total
        weights = {"property": 0.35, "market": 0.25, "deal": 0.20, "borrower": 0.20}
        total_score = sum(pillar_scores[k] * weights[k] for k in weights)
        total_score = max(0, min(100, total_score))
        score = round(total_score)

        # Map to tier
        tier = self._score_to_tier(score)

        # Determine max LTV, rate, points based on tier + fine-tuning
        max_ltv = self._compute_max_ltv(tier, score)
        rate = self._compute_rate(tier, score)
        points = self._compute_points(tier, score)

        result = {
            "score": score,
            "tier": tier,
            "max_ltv": max_ltv,
            "rate": rate,
            "points": points,
            "flags": flags,
            "pillar_scores": pillar_scores,
            "property_score": property_score,
            "market_score": market_score,
            "deal_score": deal_score_raw,
            "borrower_score": borrower_score,
        }

        logger.info(
            "score_deal result: SCORE=%d TIER=%s max_ltv=%.0f%% rate=%.2f%% points=%.1f flags=%s",
            score,
            tier,
            max_ltv * 100,
            rate,
            points,
            flags,
        )
        return result

    # ------------------------------------------------------------------
    # Stage 1 — Gating Rules
    # ------------------------------------------------------------------

    def _run_gating_rules(
        self,
        property_data: Dict[str, Any],
        deal_data: Dict[str, Any],
        borrower_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check all mandatory gating rules.  Return ``{"passed": bool,
        "reasons": list[str]}``."""

        reasons: List[str] = []

        # 1. Minimum completed flips
        completed_flips = borrower_data.get("completed_flips", 0)
        if completed_flips < MIN_COMPLETED_FLIPS:
            reasons.append(
                f"Completed flips ({completed_flips}) below minimum ({MIN_COMPLETED_FLIPS})"
            )

        # 2. Maximum LTV / ARV
        arv = property_data.get("arv_mid", 0)
        purchase_price = deal_data.get("purchase_price", 0)
        rehab_budget = deal_data.get("rehab_budget", 0)
        loan_amount = deal_data.get("loan_amount", 0)
        if arv > 0:
            if loan_amount > 0:
                ltv = loan_amount / arv
            else:
                ltv = (purchase_price + rehab_budget) / arv
            if ltv > MAX_LTV_ARV:
                reasons.append(
                    f"LTV/ARV ({ltv:.1%}) exceeds maximum ({MAX_LTV_ARV:.0%})"
                )

        # 3. Minimum borrower equity (skin in the game)
        total_cost = purchase_price + rehab_budget
        if loan_amount > 0 and total_cost > 0:
            equity = (total_cost - loan_amount) / total_cost
            if equity < MIN_BORROWER_EQUITY:
                reasons.append(
                    f"Borrower equity ({equity:.1%}) below minimum ({MIN_BORROWER_EQUITY:.0%})"
                )
        elif total_cost > 0 and loan_amount == 0:
            # If no loan_amount specified, assume max LTV loan
            pass

        # 4. Not owner-occupied (check property type and occupancy)
        occupancy = property_data.get("occupancy", "investment")
        if occupancy == "owner_occupied":
            reasons.append("Owner-occupied properties are not eligible for bridge loans")

        # 5. Max rehab as % of ARV
        if arv > 0 and rehab_budget > 0:
            rehab_pct = rehab_budget / arv
            if rehab_pct > MAX_REHAB_PCT_OF_ARV:
                reasons.append(
                    f"Rehab budget ({rehab_pct:.1%} of ARV) exceeds maximum ({MAX_REHAB_PCT_OF_ARV:.0%})"
                )

        # 6. Minimum ARV
        if arv > 0 and arv < MIN_ARV:
            reasons.append(f"ARV (${arv:,.0f}) below minimum (${MIN_ARV:,.0f})")

        return {"passed": len(reasons) == 0, "reasons": reasons}

    # ------------------------------------------------------------------
    # Stage 2 — Pillar Scoring
    # ------------------------------------------------------------------

    def _score_property(
        self,
        property_data: Dict[str, Any],
        flags: List[str],
    ) -> float:
        """Score the property pillar (0–100).

        Sub-components:
          - ARV confidence: higher → better (0–25)
          - Property type favourability: SFR > Townhouse > Condo > Multi (0–20)
          - Condition / year built: newer → better (0–20)
          - Size range: 1200–2400 sqft optimal (0–15)
          - Bed/bath ratio: sensible combos preferred (0–10)
          - Bedroom count: 3–4 is ideal (0–10)
        """
        score = 0.0

        # --- ARV Confidence (0–25) ---
        conf = property_data.get("arv_confidence", 0.6)
        score += min(25, conf * 25)

        # --- Property type (0–20) ---
        ptype = property_data.get("property_type", "SFR").upper()
        type_scores = {"SFR": 20, "TOWNHOUSE": 16, "CONDO": 12, "MULTI-FAMILY": 10, "2-4 UNIT": 10}
        score += type_scores.get(ptype, 10)

        # --- Year built / condition (0–20) ---
        year = property_data.get("year_built", 1980)
        age = max(0, 2025 - year)
        if age < 5:
            score += 20
        elif age < 20:
            score += 18
        elif age < 40:
            score += 14
        elif age < 60:
            score += 10
        elif age < 80:
            score += 6
        else:
            score += 3
            flags.append(f"Property age ({age} years) may require extensive rehab")

        # --- Size range (0–15) ---
        sqft = property_data.get("sqft", 1500)
        if 1200 <= sqft <= 2400:
            score += 15
        elif 900 <= sqft <= 3000:
            score += 10
        else:
            score += 5
            flags.append(f"Non-ideal size: {sqft} sqft")

        # --- Bed/Bath ratio (0–10) ---
        beds = property_data.get("beds", 3)
        baths = property_data.get("baths", 2.0)
        ratio = baths / max(beds, 1)
        if 0.5 <= ratio <= 1.0:
            score += 10
        elif 0.4 <= ratio <= 1.2:
            score += 7
        else:
            score += 4

        # --- Bedroom count ideal range (0–10) ---
        if 3 <= beds <= 4:
            score += 10
        elif beds == 2 or beds == 5:
            score += 7
        else:
            score += 4

        return min(100, score)

    def _score_market(
        self,
        market_data: Dict[str, Any],
        property_data: Optional[Dict[str, Any]] = None,
        flags: Optional[List[str]] = None,
    ) -> float:
        """Score the market pillar (0–100).

        Sub-components:
          - Appreciation trend: higher → better (0–35)
          - Days on market: lower → better (0–25)
          - Inventory months: 2–6 is balanced, below 2 is hot (0–20)
          - Price reduction rate: lower → better (0–10)
          - Market size / MSA: proxy for liquidity (0–10)
        """
        if flags is None:
            flags = []

        score = 0.0

        # --- Monthly appreciation (0–35) ---
        appr = market_data.get("monthly_appreciation", 0.004)  # default 0.4%/mo
        if appr >= 0.008:
            score += 35
        elif appr >= 0.005:
            score += 30
        elif appr >= 0.002:
            score += 22
        elif appr >= 0.0:
            score += 14
        else:
            score += 5
            flags.append(f"Declining market: {appr:.1%}/mo appreciation")

        # --- Days on market (0–25) ---
        dom = market_data.get("days_on_market", 30)
        if dom <= 15:
            score += 25
        elif dom <= 30:
            score += 22
        elif dom <= 45:
            score += 17
        elif dom <= 60:
            score += 12
        elif dom <= 90:
            score += 7
        else:
            score += 3
            flags.append(f"Slow market: {dom} days on market avg")

        # --- Inventory months (0–20) ---
        inv = market_data.get("inventory_months", 4.0)
        if inv <= 2.0:
            score += 20  # hot market
        elif inv <= 4.0:
            score += 18
        elif inv <= 6.0:
            score += 15
        elif inv <= 9.0:
            score += 10
        else:
            score += 5
            flags.append(f"High inventory: {inv} months supply")

        # --- Price reduction % (0–10) ---
        price_red = market_data.get("price_reduction_pct", 0.25)
        if price_red <= 0.15:
            score += 10
        elif price_red <= 0.25:
            score += 8
        elif price_red <= 0.35:
            score += 5
        else:
            score += 3

        # --- MSA population / liquidity proxy (0–10) ---
        pop = market_data.get("msa_population", 500000)
        if pop >= 2000000:
            score += 10
        elif pop >= 500000:
            score += 8
        elif pop >= 200000:
            score += 6
        elif pop >= 100000:
            score += 4
        else:
            score += 2
            flags.append(f"Small MSA: population {pop:,}")

        return min(100, score)

    def _score_deal_structure(
        self,
        property_data: Dict[str, Any],
        deal_data: Dict[str, Any],
        flags: List[str],
    ) -> float:
        """Score the deal-structure pillar (0–100).

        Sub-components:
          - LTV/ARV: lower is safer (0–25)
          - Profit margin / spread: purchase + rehab vs ARV (0–25)
          - Rehab complexity: moderate is best (0–20)
          - Hold time vs market: shorter is less risky (0–15)
          - Exit strategy clarity: sale/refi is clearer (0–15)
        """
        score = 0.0
        arv = property_data.get("arv_mid", 200000)
        purchase = deal_data.get("purchase_price", 150000)
        rehab = deal_data.get("rehab_budget", 30000)
        loan_amount = deal_data.get("loan_amount", purchase + rehab)
        total_cost = purchase + rehab

        # --- LTV / ARV (0–25) ---
        if arv > 0:
            ltv = loan_amount / arv if loan_amount > 0 else total_cost / arv
            if ltv <= 0.50:
                score += 25
            elif ltv <= 0.60:
                score += 22
            elif ltv <= 0.65:
                score += 18
            elif ltv <= 0.70:
                score += 13
            else:
                score += 5
                flags.append(f"High LTV/ARV: {ltv:.0%}")

        # --- Profit margin / spread (0–25) ---
        if arv > 0 and total_cost > 0:
            margin = (arv - total_cost) / arv
            if margin >= 0.30:
                score += 25
            elif margin >= 0.22:
                score += 22
            elif margin >= 0.15:
                score += 18
            elif margin >= 0.10:
                score += 13
            elif margin > 0:
                score += 7
            else:
                score += 2
                flags.append(f"Thin margin: {margin:.0%} spread")
        else:
            score += 10  # neutral

        # --- Rehab complexity (0–20) ---
        complexity = deal_data.get("rehab_complexity", 2)
        # 1 = cosmetic only → lower risk of overruns
        if complexity <= 1:
            score += 20
        elif complexity == 2:
            score += 18
        elif complexity == 3:
            score += 14
        elif complexity == 4:
            score += 8
        else:
            score += 3
            flags.append("Complex rehab (5/5) — high risk of overruns")

        # --- Hold time (0–15) ---
        hold_months = deal_data.get("projected_hold_months", 6)
        if hold_months <= 4:
            score += 15
        elif hold_months <= 6:
            score += 13
        elif hold_months <= 9:
            score += 10
        elif hold_months <= 12:
            score += 6
        else:
            score += 3
            flags.append(f"Long projected hold: {hold_months} months")

        # --- Exit strategy (0–15) ---
        exit_strat = (deal_data.get("exit_strategy") or "sale").lower()
        if exit_strat == "sale":
            score += 15
        elif exit_strat in ("refinance", "refi"):
            score += 12
        elif exit_strat == "rent":
            score += 9
        else:
            score += 6
            flags.append(f"Unclear exit strategy: {exit_strat}")

        return min(100, score)

    def _score_borrower(
        self,
        borrower_data: Dict[str, Any],
        flags: List[str],
    ) -> float:
        """Score the borrower pillar (0–100).

        Sub-components:
          - Completed flips / experience (0–30)
          - Credit score (0–25)
          - Years experience (0–20)
          - Active projects / capacity (0–15)
          - Financial strength: net worth + liquidity (0–10)
        """
        score = 0.0

        # --- Completed flips (0–30) ---
        flips = borrower_data.get("completed_flips", 0)
        if flips >= 10:
            score += 30
        elif flips >= 6:
            score += 27
        elif flips >= 4:
            score += 22
        elif flips >= 2:
            score += 15
        else:
            score += 5
            flags.append(f"Low flip count: {flips} completed")

        # --- Credit score (0–25) ---
        fico = borrower_data.get("credit_score", 680)
        if fico >= 740:
            score += 25
        elif fico >= 700:
            score += 22
        elif fico >= 680:
            score += 19
        elif fico >= 650:
            score += 14
        elif fico >= 620:
            score += 9
        else:
            score += 4
            flags.append(f"Low credit score: {fico}")

        # --- Years experience (0–20) ---
        years = borrower_data.get("years_experience", 0)
        if years >= 5:
            score += 20
        elif years >= 3:
            score += 17
        elif years >= 2:
            score += 13
        elif years >= 1:
            score += 8
        else:
            score += 3
            flags.append(f"Limited experience: {years:.1f} yrs")

        # --- Active projects / capacity (0–15) ---
        active = borrower_data.get("current_active_projects", 0)
        if active == 0:
            score += 15
        elif active <= 2:
            score += 12
        elif active <= 4:
            score += 8
        else:
            score += 3
            flags.append(f"High active project count: {active} — capacity risk")

        # --- Financial strength (0–10) ---
        net_worth = borrower_data.get("net_worth", 0)
        liquid = borrower_data.get("liquid_assets", 0)
        total_cost = 200000  # approximate
        # Liquid assets relative to project
        if liquid >= total_cost * 0.50:
            score += 10
        elif liquid >= total_cost * 0.25:
            score += 7
        elif liquid >= total_cost * 0.10:
            score += 4
        else:
            score += 2
            if liquid < 25000:
                flags.append("Low liquidity relative to project size")

        # Bonus for net worth
        if net_worth >= 1000000:
            score += 0  # already strong

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
        """Determine maximum LTV/ARV for the given tier and score.

        Within each tier, fine-tuning: higher scores get slightly higher LTV.
        """
        base_ltv = {
            "A": 0.70,
            "B": 0.70,
            "C": 0.65,
            "D": 0.60,
            "R": 0.0,
        }
        max_ltv = base_ltv.get(tier, 0.0)
        if tier in ("A", "B") and score >= 90:
            max_ltv = 0.70
        elif tier == "B" and score >= 78:
            max_ltv = 0.70
        elif tier == "C" and score >= 62:
            max_ltv = 0.65
        elif tier == "D" and score >= 48:
            max_ltv = 0.60
        return max_ltv

    @staticmethod
    def _compute_rate(tier: str, score: int) -> float:
        """Compute the interest rate for the tier and score.

        Higher scores within a tier get a slight rate reduction.
        """
        base = _BASE_RATES.get(tier, 0.0)
        if tier == "R":
            return 0.0
        # Within-tier fine-tuning: ±0.25% based on where score falls in range
        for (low, high), _tier in TIER_MAP.items():
            if _tier == tier:
                range_span = high - low
                if range_span > 0:
                    position = (score - low) / range_span  # 0=bottom, 1=top
                    rate = base - (position - 0.5) * 0.50
                    return round(max(base - 0.75, min(base + 0.75, rate)), 2)
        return round(base, 2)

    @staticmethod
    def _compute_points(tier: str, score: int) -> float:
        """Compute origination points for the tier and score.

        Higher scores within a tier get a slight reduction in points.
        """
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
