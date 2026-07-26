"""
BridgeStone Capital — Property Valuator (valuator.py)

Estimates the After-Repair Value (ARV) of a property using comparable sales data.
Uses ATTOM API when an API key is configured; otherwise falls back to deterministic
mock data seeded from the property address for reproducibility.

Usage:
    from underwriting.valuator import PropertyValuator

    valuator = PropertyValuator()
    arv = valuator.estimate_arv("123 Main St, Austin TX 78701", rehab_scope="kitchen+bath remodel")
    comps = valuator.get_comps("123 Main St, Austin TX 78701")
"""

import hashlib
import logging
import os
import random
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

# Ensure the project root is on the path so we can import config
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: deterministic pseudo-random from a string seed
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
# PropertyValuator
# ---------------------------------------------------------------------------

class PropertyValuator:
    """Estimates ARV (After-Repair Value) for residential investment properties.

    When ``ATTOM_API_KEY`` is set in the environment / Config, live API calls are
    attempted.  When the key is missing or the API call fails, the class falls back
    to realistic mock data generated deterministically from the property address.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate_arv(
        self,
        address: str,
        rehab_scope: Optional[str] = None,
        radius: float = 1.0,
        months_back: int = 6,
    ) -> Dict[str, Any]:
        """Return ARV estimate (mid, low, high), confidence, and comparable sales.

        Parameters
        ----------
        address : str
            Full street address including city, state, zip (e.g.
            ``"123 Main St, Austin TX 78701"``).
        rehab_scope : str, optional
            Description of planned rehab work.  Used to estimate the after-repair
            premium (e.g. ``"kitchen+bath full remodel"``).
        radius : float
            Search radius in miles for comparable sales.  Default 1.0.
        months_back : int
            Look-back window in months for comparable sales.  Default 6.

        Returns
        -------
        dict
            Keys: ``arv_mid`` (float), ``arv_low`` (float), ``arv_high`` (float),
            ``confidence`` (float 0–1), ``comps`` (list of dict).
        """
        logger.info(
            "estimate_arv(address=%r, rehab_scope=%r, radius=%.1f, months_back=%d)",
            address,
            rehab_scope,
            radius,
            months_back,
        )

        # 1. Try live ATTOM API
        api_key = Config.ATTOM_API_KEY
        if api_key:
            comps = self._fetch_comps_from_attom(address, radius, months_back)
        else:
            logger.info("No ATTOM_API_KEY — using deterministic mock comps")
            comps = None

        # 2. Fall back to mock data
        if not comps:
            comps = self.get_comps(address, radius, months_back)

        # 3. Adjust comps for market trend (mock MSA data if no live API)
        comps = self.market_trend_adjustment(comps)

        # 4. Derive ARV from comps
        if not comps:
            logger.warning("No comps available for %r — returning nominal estimate", address)
            return {
                "arv_mid": 200000.0,
                "arv_low": 180000.0,
                "arv_high": 220000.0,
                "confidence": 0.3,
                "comps": [],
            }

        prices = sorted(c["sale_price"] for c in comps)
        n = len(prices)

        arv_mid = sum(prices) / n if n else 0.0

        # Simple trimmed-mean-like approach for low/high
        if n >= 5:
            arv_low = sum(prices[: n // 3]) / max(n // 3, 1)
            arv_high = sum(prices[-(n // 3) :]) / max(n // 3, 1)
        elif n >= 2:
            arv_low = prices[0]
            arv_high = prices[-1]
        else:
            arv_low = prices[0] * 0.90
            arv_high = prices[0] * 1.10

        # Confidence: based on number of comps and how tight the range is
        range_pct = (arv_high - arv_low) / max(arv_mid, 1)
        count_component = min(n / 10.0, 0.5)
        spread_component = max(0.0, 0.5 - range_pct * 2.5)
        confidence = min(1.0, max(0.1, count_component + spread_component))

        # 5. Apply rehab premium if scope provided
        if rehab_scope:
            rehab_factor = self._rehab_premium(rehab_scope)
            arv_mid *= rehab_factor
            arv_low *= rehab_factor * 0.95
            arv_high *= rehab_factor * 1.05
            logger.info("Applied rehab premium: factor=%.3f", rehab_factor)

        result = {
            "arv_mid": round(arv_mid, 2),
            "arv_low": round(arv_low, 2),
            "arv_high": round(arv_high, 2),
            "confidence": round(confidence, 3),
            "comps": comps,
        }

        logger.info(
            "ARV result: mid=$%.0f  low=$%.0f  high=$%.0f  confidence=%.2f  %d comps",
            result["arv_mid"],
            result["arv_low"],
            result["arv_high"],
            result["confidence"],
            len(comps),
        )
        return result

    def get_comps(
        self,
        address: str,
        radius: float = 1.0,
        months_back: int = 6,
    ) -> List[Dict[str, Any]]:
        """Return a list of comparable sales near *address*.

        Falls back to deterministic mock data when the ATTOM API is unavailable.

        Parameters
        ----------
        address : str
            Subject property address.
        radius : float
            Search radius in miles.
        months_back : int
            Look-back window in months.

        Returns
        -------
        list[dict]
            Each comp has keys: ``address``, ``distance_mi``, ``sale_date``,
            ``sale_price``, ``sqft``, ``beds``, ``baths``, ``year_built``,
            ``price_per_sqft``, ``property_type``.
        """
        logger.info("get_comps(address=%r, radius=%.1f, months_back=%d)", address, radius, months_back)
        return self._generate_mock_comps(address, radius, months_back)

    def market_trend_adjustment(
        self,
        comps: List[Dict[str, Any]],
        msa_data: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Apply market appreciation / depreciation adjustments to comp sales prices.

        Uses mock MSA-level trend data (monthly appreciation rate) when
        ``msa_data`` is ``None``.  Each comp is adjusted from its sale date to
        the present.

        Parameters
        ----------
        comps : list[dict]
            Comparable sales (as returned by ``get_comps``).
        msa_data : dict, optional
            Live MSA data with key ``monthly_appreciation``.

        Returns
        -------
        list[dict]
            The same list with ``sale_price`` adjusted in-place.
        """
        if msa_data is None:
            # Mock MSA trend: assume ~0.25–0.75 % monthly appreciation
            seed = _seed_from_address(comps[0]["address"]) if comps else 42
            monthly_rate = _deterministic_random(seed + 999, 0.002, 0.006)
            msa_data = {"monthly_appreciation": monthly_rate}

        rate = msa_data.get("monthly_appreciation", 0.004)

        for comp in comps:
            sale_dt = datetime.strptime(comp["sale_date"], "%Y-%m-%d")
            months_ago = (datetime.utcnow() - sale_dt).days / 30.0
            adj_factor = (1 + rate) ** max(0, months_ago)
            original = comp["sale_price"]
            comp["sale_price"] = round(original * adj_factor, 2)
            comp["price_per_sqft"] = round(
                comp["sale_price"] / max(comp.get("sqft", 1), 1), 2
            )

        logger.info(
            "Market trend adjustment: %.3f%%/mo applied to %d comps",
            rate * 100,
            len(comps),
        )
        return comps

    # ------------------------------------------------------------------
    # ATTOM API integration
    # ------------------------------------------------------------------

    def _fetch_comps_from_attom(
        self,
        address: str,
        radius: float,
        months_back: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """Attempt to fetch comparable sales from the ATTOM property API.

        Returns ``None`` on any kind of failure (network, auth, parsing), which
        triggers fallback to mock data.
        """
        api_key = Config.ATTOM_API_KEY
        if not api_key:
            return None

        # Parse address into components
        parts = [p.strip() for p in address.split(",")]
        address1 = parts[0] if len(parts) > 0 else address
        city_state_zip = parts[1] if len(parts) > 1 else ""
        city_parts = city_state_zip.split()
        zip_code = city_parts[-1] if city_parts else ""
        state = city_parts[-2] if len(city_parts) >= 2 else ""
        city = " ".join(city_parts[:-2]) if len(city_parts) >= 3 else ""

        url = f"{Config.ATTOM_BASE_URL}/sale/snapshot"
        headers = {
            "apikey": api_key,
            "Accept": "application/json",
        }
        params = {
            "address1": address1,
            "address2": f"{city}, {state} {zip_code}",
            "radius": str(radius),
            "minsaleamt": "50000",
            "maxsaleamt": "3000000",
        }

        try:
            logger.info("Calling ATTOM API: %s", url)
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            comps = self._parse_attom_response(data, months_back)
            logger.info("ATTOM returned %d valid comps", len(comps))
            return comps if comps else None
        except Exception as exc:
            logger.warning("ATTOM API call failed: %s — falling back to mock", exc)
            return None

    def _parse_attom_response(
        self,
        data: Dict[str, Any],
        months_back: int,
    ) -> List[Dict[str, Any]]:
        """Convert ATTOM JSON into the standard comps list format."""
        comps = []
        cutoff = datetime.utcnow() - timedelta(days=months_back * 30)

        for sale in data.get("property", [])[:15]:
            sale_date_str = sale.get("sale", {}).get("saleTransDate", "")
            try:
                sale_date = datetime.strptime(sale_date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if sale_date < cutoff:
                continue

            address_info = sale.get("address", {})
            building = sale.get("building", {}).get("size", {})
            lot = sale.get("lot", {})

            comp = {
                "address": (
                    f"{address_info.get('line1', '')}, "
                    f"{address_info.get('locality', '')} "
                    f"{address_info.get('countrySubd', '')} "
                    f"{address_info.get('postal1', '')}"
                ).strip(", "),
                "distance_mi": 0.0,
                "sale_date": sale_date_str,
                "sale_price": float(sale.get("sale", {}).get("saleAmt", 0)),
                "sqft": int(building.get("livingsize", 0)),
                "beds": int(building.get("beds", 0)),
                "baths": float(building.get("baths", 0)),
                "year_built": int(lot.get("yearBuilt", 0)),
                "price_per_sqft": 0.0,
                "property_type": building.get("propertyType", "SFR"),
            }
            comp["price_per_sqft"] = round(
                comp["sale_price"] / max(comp["sqft"], 1), 2
            )
            comps.append(comp)

        return comps

    # ------------------------------------------------------------------
    # Deterministic mock data generation
    # ------------------------------------------------------------------

    def _generate_mock_comps(
        self,
        address: str,
        radius: float,
        months_back: int,
    ) -> List[Dict[str, Any]]:
        """Generate 5–9 deterministic, realistic comparable sales from *address*.

        Each property hash produces a different but reproducible set.  The comps
        are centered around a base price derived from the address hash.
        """
        seed = _seed_from_address(address)
        rng = random.Random(seed)

        # Base price determined by address hash (ranges $75K – $800K)
        base_price = _deterministic_random(seed, 80000, 750000)

        # Number of comps: 5–9
        num_comps = rng.randint(5, 9)

        # Subject property characteristics (deterministic from seed + offsets)
        subject_sqft = _deterministic_randint(seed + 10, 900, 3200)
        subject_beds = _deterministic_randint(seed + 20, 2, 5)
        subject_baths = _deterministic_random(seed + 30, 1, 3.5)
        # Round baths to nearest 0.5
        subject_baths = round(subject_baths * 2) / 2

        comps = []
        for i in range(num_comps):
            comp_seed = seed + 100 + i * 7

            # Slightly vary characteristics around subject
            sqft = max(600, subject_sqft + _deterministic_randint(comp_seed, -300, 300))
            beds = max(1, subject_beds + _deterministic_randint(comp_seed + 1, -1, 1))
            baths_raw = subject_baths + _deterministic_random(comp_seed + 2, -1.0, 1.0)
            baths = max(1.0, round(baths_raw * 2) / 2)

            # Sale date: random in the look-back window
            days_ago = _deterministic_random(comp_seed + 3, 5, months_back * 30)
            sale_date = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

            # Sale price: base + characteristics adjustments ± 15%
            sqft_factor = (sqft / max(subject_sqft, 1)) ** 0.6
            price = base_price * sqft_factor * _deterministic_random(comp_seed + 4, 0.85, 1.15)

            # Distance: within radius
            distance = round(_deterministic_random(comp_seed + 5, 0.05, radius), 2)

            # Year built
            year_built = _deterministic_randint(comp_seed + 6, 1950, 2024)

            comps.append({
                "address": f"{100 + i * 10} Comp St, Anytown XX {10000 + i * 11:05d}",
                "distance_mi": distance,
                "sale_date": sale_date,
                "sale_price": round(price, 2),
                "sqft": sqft,
                "beds": beds,
                "baths": baths,
                "year_built": year_built,
                "price_per_sqft": round(price / max(sqft, 1), 2),
                "property_type": rng.choice(["SFR", "Condo", "Townhouse"]),
            })

        # Sort by sale date descending
        comps.sort(key=lambda c: c["sale_date"], reverse=True)

        logger.info(
            "Generated %d mock comps for %r (base_price=$%.0f)",
            len(comps),
            address,
            base_price,
        )
        return comps

    # ------------------------------------------------------------------
    # Rehab premium estimation
    # ------------------------------------------------------------------

    @staticmethod
    def _rehab_premium(rehab_scope: str) -> float:
        """Estimate the ARV multiplier from a rehab scope description.

        Returns a factor like 1.08 (8% premium) to 1.35 (35% premium).
        """
        scope_lower = rehab_scope.lower()

        # High-end / full gut rehab
        if any(kw in scope_lower for kw in ("full gut", "gut rehab", "complete remodel", "high-end", "luxury")):
            return 1.30
        # Major: kitchen + bath
        if "kitchen" in scope_lower and "bath" in scope_lower:
            return 1.18
        # Kitchen only
        if "kitchen" in scope_lower:
            return 1.12
        # Bath only
        if "bath" in scope_lower:
            return 1.08
        # Cosmetic
        if any(kw in scope_lower for kw in ("cosmetic", "paint", "floor", "carpet", "light")):
            return 1.05
        # Moderate
        if any(kw in scope_lower for kw in ("update", "remodel", "renovation")):
            return 1.10

        return 1.06  # default modest premium
