"""
ResaleScorer — Deal potential scoring for Facebook Marketplace listings.

Scores each listing 0-150 based on:
  - Price vs. category median (up to 40 pts)
  - Urgency keywords (up to 20 pts)
  - Listing recency (up to 15 pts)
  - Image count / legitimacy (up to 10 pts)
  - Distance from user (up to 15 pts)
  - Category weight bonus/penalty
  - Profit boost
  - Geo distance penalty
  - Practicality boost

Returns a score + detailed breakdown dict per listing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from geo import score_geo as _score_geo
except ImportError:
    def _score_geo(listing):
        return {
            "distance_miles": None,
            "travel_tier": "unknown",
            "distance_penalty": 0,
            "effective_profit_after_travel": None,
            "geocoded": False,
        }

# ---------------------------------------------------------------------------
# Category price reference table
# ---------------------------------------------------------------------------
# Approximate US median resale prices for common categories on Facebook
# Marketplace / eBay completed listings (2024 data).
# Values are (typical_low, typical_median, typical_high) in USD.

CATEGORY_PRICE_REFERENCE: dict[str, tuple[float, float, float]] = {
    # Electronics
    "iphone": (200, 500, 900),
    "iphone 15": (500, 750, 900),
    "iphone 14": (350, 550, 750),
    "iphone 13": (250, 400, 600),
    "iphone 12": (150, 280, 450),
    "samsung galaxy": (150, 350, 700),
    "macbook": (400, 800, 1800),
    "macbook pro": (600, 1100, 2200),
    "macbook air": (400, 700, 1400),
    "ipad": (150, 350, 800),
    "laptop": (150, 400, 1200),
    "ps5": (300, 450, 500),
    "ps4": (100, 200, 300),
    "xbox series x": (280, 380, 500),
    "xbox one": (80, 150, 250),
    "nintendo switch": (150, 250, 350),
    "gaming pc": (300, 700, 2000),
    "graphics card": (150, 400, 1200),
    "gpu": (150, 400, 1200),
    "monitor": (50, 150, 400),
    "tv": (50, 200, 800),
    "camera": (100, 350, 1200),
    "drone": (100, 300, 800),
    "airpods": (40, 100, 200),
    "headphones": (30, 120, 400),
    "keyboard": (30, 100, 300),
    "mechanical keyboard": (50, 150, 400),
    # Furniture
    "couch": (50, 200, 600),
    "sofa": (50, 200, 600),
    "bed frame": (50, 150, 500),
    "mattress": (50, 200, 600),
    "desk": (50, 175, 500),
    "dresser": (40, 150, 400),
    "bookshelf": (20, 80, 250),
    "dining table": (50, 200, 700),
    "dining set": (100, 350, 1000),
    "recliner": (50, 200, 600),
    "sectional": (100, 400, 1200),
    # Tools
    "dewalt": (50, 200, 600),
    "milwaukee": (50, 200, 600),
    "makita": (50, 200, 600),
    "table saw": (100, 350, 1000),
    "drill": (30, 100, 300),
    "circular saw": (40, 120, 350),
    "air compressor": (50, 150, 500),
    "generator": (200, 600, 2000),
    "pressure washer": (80, 250, 700),
    "lawn mower": (50, 200, 600),
    "riding mower": (200, 800, 2500),
    # Musical instruments
    "guitar": (80, 250, 800),
    "electric guitar": (100, 300, 1000),
    "acoustic guitar": (80, 250, 600),
    "bass guitar": (100, 300, 900),
    "piano": (200, 800, 3000),
    "keyboard piano": (100, 300, 1000),
    "drums": (150, 400, 1500),
    "drum kit": (150, 400, 1500),
    "amplifier": (80, 250, 800),
    "saxophone": (200, 600, 2000),
    "violin": (100, 300, 1000),
    # Sporting goods
    "bicycle": (80, 250, 800),
    "mountain bike": (100, 350, 1200),
    "road bike": (100, 400, 1500),
    "treadmill": (100, 350, 1000),
    "elliptical": (100, 300, 900),
    "weights": (50, 150, 400),
    "dumbbell": (20, 60, 200),
    "barbell": (50, 150, 400),
    "kayak": (200, 500, 1500),
    "surfboard": (100, 350, 900),
    "golf clubs": (50, 250, 800),
    # Vehicles (rough estimates for parts/accessories)
    "car parts": (20, 100, 400),
    "wheels": (100, 300, 800),
    "tires": (50, 150, 400),
    # Collectibles / vintage
    "sneakers": (50, 150, 500),
    "jordan": (100, 250, 600),
    "vintage": (50, 200, 800),
    "antique": (50, 250, 1000),
    # --- High-ticket: vehicles ---
    "car": (2000, 8000, 25000),
    "truck": (3000, 12000, 35000),
    "pickup truck": (4000, 15000, 38000),
    "semi truck": (15000, 45000, 120000),
    "semi": (15000, 45000, 120000),
    "motorcycle": (1500, 5000, 15000),
    "harley": (3000, 8000, 25000),
    "harley davidson": (3000, 8000, 25000),
    "kawasaki": (1500, 4500, 12000),
    "yamaha": (1500, 4500, 12000),
    "honda motorcycle": (1500, 4000, 10000),
    "dirt bike": (1000, 3000, 8000),
    "atv": (1000, 4000, 12000),
    "side by side": (3000, 8000, 20000),
    "rzr": (5000, 12000, 25000),
    "trailer": (1000, 4000, 15000),
    "utility trailer": (500, 2500, 8000),
    "enclosed trailer": (1500, 5000, 15000),
    "dump trailer": (2000, 6000, 20000),
    "flatbed trailer": (1000, 3500, 12000),
    "cargo trailer": (1000, 4000, 12000),
    "boat": (2000, 8000, 30000),
    "pontoon": (5000, 18000, 50000),
    "rv": (10000, 35000, 100000),
    "motorhome": (15000, 45000, 120000),
    # --- High-ticket: heavy equipment (GovPlanet/PublicSurplus focus) ---
    "excavator": (10000, 35000, 120000),
    "mini excavator": (5000, 18000, 50000),
    "backhoe": (8000, 25000, 80000),
    "skid steer": (6000, 20000, 60000),
    "bobcat": (6000, 20000, 60000),
    "bulldozer": (15000, 50000, 150000),
    "wheel loader": (15000, 50000, 150000),
    "front loader": (8000, 30000, 90000),
    "forklift": (4000, 12000, 40000),
    "telehandler": (15000, 45000, 120000),
    "boom lift": (10000, 30000, 90000),
    "scissor lift": (5000, 18000, 50000),
    "tractor": (5000, 20000, 80000),
    "john deere": (5000, 20000, 80000),
    "kubota": (4000, 15000, 60000),
    "caterpillar": (15000, 60000, 200000),
    "cat": (15000, 60000, 200000),
    "komatsu": (12000, 45000, 150000),
    "crane": (20000, 80000, 250000),
    "aerial lift": (8000, 25000, 80000),
    "compactor": (5000, 20000, 60000),
    "road roller": (8000, 25000, 80000),
    "paver": (15000, 50000, 150000),
    "generator": (500, 2500, 15000),
    "industrial generator": (3000, 15000, 60000),
    "welder": (300, 1200, 5000),
    "air compressor": (100, 600, 3000),
    "industrial compressor": (1000, 5000, 20000),
    "pressure washer": (100, 400, 2000),
    "industrial pressure washer": (500, 2500, 10000),
    "wood chipper": (2000, 8000, 25000),
    "stump grinder": (2000, 8000, 25000),
    "trencher": (3000, 12000, 40000),
    # --- Tools (eBay/GovPlanet) ---
    "snap on": (200, 1000, 8000),
    "snap-on": (200, 1000, 8000),
    "tool box": (100, 500, 3000),
    "tool chest": (100, 500, 3000),
    "table saw": (200, 600, 2500),
    "band saw": (100, 400, 1500),
    "miter saw": (100, 350, 1200),
    "lathe": (500, 2500, 10000),
    "metal lathe": (800, 4000, 15000),
    "mill": (1000, 5000, 20000),
    "milling machine": (1000, 5000, 20000),
    "drill press": (100, 400, 1500),
    "plasma cutter": (300, 1200, 5000),
    "tig welder": (400, 1800, 8000),
    "mig welder": (200, 800, 3000),
    "stick welder": (100, 400, 1500),
    # Generic fallback
    "__default__": (10, 100, 500),
}


def get_category_median(title: str, category: Optional[str] = None) -> float:
    """
    Look up the median price for a listing based on its title keywords
    and/or category label. Returns the median value.
    """
    text = f"{title} {category or ''}".lower()

    # Try multi-word matches first (more specific)
    best_match: Optional[tuple[float, float, float]] = None
    best_match_len = 0

    for key, prices in CATEGORY_PRICE_REFERENCE.items():
        if key == "__default__":
            continue
        if key in text and len(key) > best_match_len:
            best_match = prices
            best_match_len = len(key)

    if best_match:
        return best_match[1]

    return CATEGORY_PRICE_REFERENCE["__default__"][1]


# ---------------------------------------------------------------------------
# Urgency keywords
# ---------------------------------------------------------------------------

URGENCY_KEYWORDS: dict[str, int] = {
    # High urgency (8-10 pts each, capped at 20 total)
    "must sell": 10,
    "need gone": 10,
    "moving": 8,
    "relocating": 8,
    "eviction": 10,
    "house sold": 9,
    "selling house": 8,
    "divorce": 9,
    "estate sale": 7,
    # Medium urgency (5-7 pts)
    "today only": 8,
    "liquidating": 8,
    "urgent": 7,
    "obo": 6,
    "or best offer": 6,
    "best offer": 5,
    "firm price drop": 6,
    "price drop": 5,
    "reduced": 5,
    "firm but": 4,
    "quick sale": 7,
    "asap": 7,
    "need cash": 7,
    "need money": 7,
    "emergency": 8,
    "fire sale": 8,
    # Low urgency (2-4 pts)
    "negotiable": 3,
    "open to offers": 3,
    "make offer": 3,
    "will consider": 2,
    "flexible": 2,
    "motivated": 4,
}

# ---------------------------------------------------------------------------
# Category weights
# ---------------------------------------------------------------------------

CATEGORY_WEIGHTS: dict[str, int] = {
    "vehicles": 20,
    "motorcycles": 20,
    "cars+trucks": 20,
    "equipment": 25,
    "heavy_equipment": 25,
    "tools": 10,
    "electronics": 10,
    "bulk": -15,
    "general": -5,
    "unknown": -20,
}


def _get_category_weight(category: str, title: str) -> int:
    if category:
        key = category.lower().strip()
        if key in CATEGORY_WEIGHTS:
            return CATEGORY_WEIGHTS[key]
    return 0


# ---------------------------------------------------------------------------
# Confidence score
# ---------------------------------------------------------------------------

def confidence_score(listing: dict) -> float:
    score = 0.5
    title = (listing.get("title") or "").lower().strip()
    category = (listing.get("category") or "").lower().strip()
    price = listing.get("price")
    posted_at_raw = listing.get("posted_at")

    # Title clarity
    words = [w for w in title.split() if len(w) > 1]
    if len(words) < 3 or any(t in title for t in ("misc", "stuff", "lot", "junk")):
        score -= 0.2
    elif len(words) >= 4:
        score += 0.2

    # Category match
    high_conf_cats = {"vehicles", "motorcycles", "cars+trucks", "equipment", "heavy_equipment", "tools", "electronics"}
    low_conf_cats = {"bulk", "unknown", "general"}
    if category in high_conf_cats:
        score += 0.3
    elif category in low_conf_cats:
        score -= 0.2

    # Price anomaly vs median
    if price is not None and price > 0:
        median = get_category_median(title, category)
        if median > 0:
            if price < median * 0.5:
                score += 0.3
            elif price > median:
                score -= 0.2

    # Recency
    posted_at: Optional[datetime] = None
    if isinstance(posted_at_raw, str):
        try:
            posted_at = datetime.fromisoformat(posted_at_raw)
        except Exception:
            pass
    elif isinstance(posted_at_raw, datetime):
        posted_at = posted_at_raw

    if posted_at is not None:
        age_hours = (datetime.utcnow() - posted_at).total_seconds() / 3600
        if age_hours < 6:
            score += 0.2
        elif age_hours > 168:  # 7 days
            score -= 0.1

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Profit estimation
# ---------------------------------------------------------------------------

def estimate_profit(price: float, category: str, title: str) -> dict:
    median = get_category_median(title, category)
    estimated_resale_low = median * 0.7
    estimated_resale_high = median * 1.1
    estimated_profit_low = estimated_resale_low - price
    estimated_profit_high = estimated_resale_high - price

    profit_boost = 0
    if estimated_profit_low >= 500:
        profit_boost = 15
    elif estimated_resale_low > 0 and (estimated_resale_low - price) / estimated_resale_low >= 0.30:
        profit_boost = 10

    return {
        "median": median,
        "estimated_resale_low": estimated_resale_low,
        "estimated_resale_high": estimated_resale_high,
        "estimated_profit_low": estimated_profit_low,
        "estimated_profit_high": estimated_profit_high,
        "profit_boost": profit_boost,
    }


# ---------------------------------------------------------------------------
# Practicality layer
# ---------------------------------------------------------------------------

HIGH_RESALE = frozenset({
    "vehicle", "motorcycle", "truck", "equipment", "tool", "lathe",
    "generator", "compressor", "welder", "trailer", "rv", "boat",
})
LOW_RESALE = frozenset({
    "junk", "random", "stuff", "lot", "misc", "bundle", "box of", "bag of", "pile",
})


def practicality_score(listing: dict) -> int:
    title = (listing.get("title") or "").lower()
    if any(w in title for w in HIGH_RESALE):
        return 10
    if any(w in title for w in LOW_RESALE):
        return -15
    return 0


# ---------------------------------------------------------------------------
# Score result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    score: float                      # 0-150 total
    price_score: float                # 0-40
    urgency_score: float              # 0-20
    recency_score: float              # 0-15
    image_score: float                # 0-10
    distance_score: float             # 0-15
    price_vs_median_pct: Optional[float]   # % below median (positive = good deal)
    category_median: Optional[float]
    matched_keywords: list[str]
    days_listed: Optional[float]
    explanation: str
    confidence: float = 0.5
    estimated_resale_low: Optional[float] = None
    estimated_resale_high: Optional[float] = None
    estimated_profit_low: Optional[float] = None
    estimated_profit_high: Optional[float] = None
    category_weight: int = 0
    urgency_matched: list = field(default_factory=list)
    profit_boost: int = 0
    # Geo fields
    distance_miles: Optional[float] = None
    travel_tier: str = "unknown"
    distance_penalty: int = 0
    effective_profit_after_travel: Optional[float] = None
    geocoded: bool = False
    # Practicality
    practicality_boost: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "price_score": round(self.price_score, 1),
            "urgency_score": round(self.urgency_score, 1),
            "recency_score": round(self.recency_score, 1),
            "image_score": round(self.image_score, 1),
            "distance_score": round(self.distance_score, 1),
            "price_vs_median_pct": (
                round(self.price_vs_median_pct, 1)
                if self.price_vs_median_pct is not None
                else None
            ),
            "category_median": self.category_median,
            "matched_keywords": self.matched_keywords,
            "days_listed": self.days_listed,
            "explanation": self.explanation,
            "confidence": round(self.confidence, 3),
            "estimated_resale_low": round(self.estimated_resale_low, 2) if self.estimated_resale_low is not None else None,
            "estimated_resale_high": round(self.estimated_resale_high, 2) if self.estimated_resale_high is not None else None,
            "estimated_profit_low": round(self.estimated_profit_low, 2) if self.estimated_profit_low is not None else None,
            "estimated_profit_high": round(self.estimated_profit_high, 2) if self.estimated_profit_high is not None else None,
            "category_weight": self.category_weight,
            "urgency_matched": self.urgency_matched,
            "profit_boost": self.profit_boost,
            "distance_miles": self.distance_miles,
            "travel_tier": self.travel_tier,
            "distance_penalty": self.distance_penalty,
            "effective_profit_after_travel": (
                round(self.effective_profit_after_travel, 2)
                if self.effective_profit_after_travel is not None
                else None
            ),
            "geocoded": self.geocoded,
            "practicality_boost": self.practicality_boost,
        }


# ---------------------------------------------------------------------------
# Scorer class
# ---------------------------------------------------------------------------

class ResaleScorer:
    """
    Score a normalized listing dict for deal/resale potential.

    Example:
        scorer = ResaleScorer(user_zip="78584", max_acceptable_distance=40)
        result = scorer.score(listing)
        print(result.score)           # e.g. 78.5
        print(result.to_dict())       # full breakdown
    """

    def __init__(
        self,
        user_zip: Optional[str] = None,
        max_acceptable_distance: float = 40.0,
        price_weight: float = 1.0,
    ) -> None:
        self.user_zip = user_zip
        self.max_acceptable_distance = max_acceptable_distance
        self.price_weight = price_weight

    def score(self, listing: dict[str, Any]) -> ScoreBreakdown:
        """
        Score a listing dict and return a ScoreBreakdown.

        Expected listing keys (all optional except title):
            title, price, location, distance, image_count,
            posted_at, description, category
        """
        title = (listing.get("title") or "").lower()
        description = (listing.get("description") or "").lower()
        combined_text = f"{title} {description}"

        price = listing.get("price")
        image_count = listing.get("image_count") or 0
        distance = listing.get("distance")
        posted_at_raw = listing.get("posted_at")
        category = listing.get("category")

        # Parse posted_at
        posted_at: Optional[datetime] = None
        if isinstance(posted_at_raw, str):
            try:
                posted_at = datetime.fromisoformat(posted_at_raw)
            except Exception:
                pass
        elif isinstance(posted_at_raw, datetime):
            posted_at = posted_at_raw

        # ---- 1. Price score (0-40) ----
        price_score, price_vs_median_pct, category_median = self._score_price(
            price=price, title=title, category=category
        )

        # ---- 2. Urgency score (0-20) ----
        urgency_score, matched_keywords = self._score_urgency(combined_text)

        # ---- 3. Recency score (0-15) ----
        recency_score, days_listed = self._score_recency(posted_at)

        # ---- 4. Image score (0-10) ----
        image_score = self._score_images(image_count)

        # ---- 5. Distance score (0-15) ----
        distance_score = self._score_distance(distance)

        # ---- Extended layers ----
        cat_weight = _get_category_weight(category or "", title)
        profit_data = estimate_profit(price or 0.0, category or "", title)
        conf = confidence_score(listing)

        # ---- Geo scoring ----
        _listing_for_geo = dict(listing)
        _listing_for_geo["estimated_profit_low"] = profit_data["estimated_profit_low"]
        geo = _score_geo(_listing_for_geo)

        # ---- Practicality ----
        prac_boost = practicality_score(listing)

        # ---- Total ----
        base_score = (
            price_score
            + urgency_score
            + recency_score
            + image_score
            + distance_score
        )
        total = (
            base_score
            + cat_weight
            + profit_data["profit_boost"]
            + geo["distance_penalty"]   # negative, naturally reduces score
            + prac_boost
        )
        total = max(0.0, min(150.0, total))

        explanation = self._build_explanation(
            price_score=price_score,
            urgency_score=urgency_score,
            recency_score=recency_score,
            image_score=image_score,
            distance_score=distance_score,
            price_vs_median_pct=price_vs_median_pct,
            category_median=category_median,
            matched_keywords=matched_keywords,
            days_listed=days_listed,
            distance=distance,
        )

        return ScoreBreakdown(
            score=total,
            price_score=price_score,
            urgency_score=urgency_score,
            recency_score=recency_score,
            image_score=image_score,
            distance_score=distance_score,
            price_vs_median_pct=price_vs_median_pct,
            category_median=category_median,
            matched_keywords=matched_keywords,
            days_listed=days_listed,
            explanation=explanation,
            confidence=conf,
            estimated_resale_low=profit_data["estimated_resale_low"],
            estimated_resale_high=profit_data["estimated_resale_high"],
            estimated_profit_low=profit_data["estimated_profit_low"],
            estimated_profit_high=profit_data["estimated_profit_high"],
            category_weight=cat_weight,
            urgency_matched=matched_keywords,
            profit_boost=profit_data["profit_boost"],
            distance_miles=geo["distance_miles"],
            travel_tier=geo["travel_tier"],
            distance_penalty=geo["distance_penalty"],
            effective_profit_after_travel=geo["effective_profit_after_travel"],
            geocoded=geo["geocoded"],
            practicality_boost=prac_boost,
        )

    # ------------------------------------------------------------------
    # Sub-scorers
    # ------------------------------------------------------------------

    def _score_price(
        self,
        price: Optional[float],
        title: str,
        category: Optional[str],
    ) -> tuple[float, Optional[float], Optional[float]]:
        """
        Score price relative to category median. 0-40 points.
        40 pts = 60%+ below median (exceptional deal)
         0 pts = at or above median (no deal)
        """
        if price is None:
            return 5.0, None, None  # Unknown price gets a small default

        if price == 0:
            return 35.0, 100.0, None  # Free item is almost always a good deal

        median = get_category_median(title, category)

        if median <= 0:
            return 5.0, None, median

        pct_below = (median - price) / median * 100  # Positive = below median

        if pct_below >= 60:
            pts = 40.0
        elif pct_below >= 45:
            pts = 32.0
        elif pct_below >= 30:
            pts = 24.0
        elif pct_below >= 20:
            pts = 16.0
        elif pct_below >= 10:
            pts = 8.0
        elif pct_below >= 0:
            pts = 4.0
        else:
            # Price is above median
            pts = max(0.0, 4.0 + pct_below * 0.1)  # Penalize overpriced items

        pts *= self.price_weight
        return min(40.0, max(0.0, pts)), pct_below, median

    def _score_urgency(
        self, text: str
    ) -> tuple[float, list[str]]:
        """Score urgency keywords in title + description. 0-20 points."""
        matched: list[str] = []
        total_pts = 0.0

        for keyword, pts in URGENCY_KEYWORDS.items():
            if keyword in text:
                matched.append(keyword)
                total_pts += pts

        return min(20.0, total_pts), matched

    def _score_recency(
        self, posted_at: Optional[datetime]
    ) -> tuple[float, Optional[float]]:
        """
        Score listing recency. 0-15 points.
        15 pts = posted within 1 hour
         0 pts = posted 14+ days ago
        """
        if posted_at is None:
            return 5.0, None  # Unknown age gets moderate score

        now = datetime.utcnow()
        age = now - posted_at
        days = age.total_seconds() / 86400

        if days < 0.042:   # < 1 hour
            pts = 15.0
        elif days < 0.25:  # < 6 hours
            pts = 13.0
        elif days < 1:     # < 1 day
            pts = 11.0
        elif days < 2:
            pts = 9.0
        elif days < 4:
            pts = 7.0
        elif days < 7:
            pts = 5.0
        elif days < 14:
            pts = 2.0
        else:
            pts = 0.0

        return pts, days

    def _score_images(self, image_count: int) -> float:
        """
        Score legitimacy via image count. 0-10 points.
        More images = seller is more serious = listing more likely legit.
        """
        if image_count == 0:
            return 0.0
        elif image_count == 1:
            return 3.0
        elif image_count == 2:
            return 6.0
        elif image_count == 3:
            return 8.0
        else:
            return 10.0

    def _score_distance(self, distance: Optional[float]) -> float:
        """
        Score proximity. 0-15 points.
        Closer = higher score (easier pickup = better deal).
        """
        if distance is None:
            return 8.0  # Unknown distance gets middle score

        max_d = self.max_acceptable_distance

        if distance <= 2:
            return 15.0
        elif distance <= 5:
            return 13.0
        elif distance <= 10:
            return 11.0
        elif distance <= 20:
            return 8.0
        elif distance <= max_d:
            # Linear decay from 8 to 2 within acceptable range
            ratio = (distance - 20) / (max_d - 20)
            return max(2.0, 8.0 - ratio * 6.0)
        else:
            # Beyond max acceptable distance
            return 0.0

    # ------------------------------------------------------------------
    # Explanation builder
    # ------------------------------------------------------------------

    def _build_explanation(
        self,
        price_score: float,
        urgency_score: float,
        recency_score: float,
        image_score: float,
        distance_score: float,
        price_vs_median_pct: Optional[float],
        category_median: Optional[float],
        matched_keywords: list[str],
        days_listed: Optional[float],
        distance: Optional[float],
    ) -> str:
        parts = []

        if price_vs_median_pct is not None:
            if price_vs_median_pct >= 30:
                parts.append(
                    f"Price is {price_vs_median_pct:.0f}% below category median "
                    f"(${category_median:.0f}) — strong deal signal."
                )
            elif price_vs_median_pct >= 0:
                parts.append(
                    f"Price is {price_vs_median_pct:.0f}% below category median "
                    f"(${category_median:.0f})."
                )
            else:
                parts.append(
                    f"Price is {abs(price_vs_median_pct):.0f}% above category median "
                    f"(${category_median:.0f}) — overpriced."
                )

        if matched_keywords:
            parts.append(
                f"Urgency signals found: {', '.join(matched_keywords)}."
            )

        if days_listed is not None:
            if days_listed < 1:
                parts.append(f"Listed {days_listed * 24:.0f}h ago — very fresh.")
            else:
                parts.append(f"Listed {days_listed:.1f} days ago.")
        else:
            parts.append("Listing age unknown.")

        if image_score >= 8:
            parts.append(f"Good image count ({image_score:.0f}/10 pts).")
        elif image_score == 0:
            parts.append("No images — legitimacy concern.")

        if distance is not None:
            parts.append(f"Distance: {distance:.1f} miles.")

        return " ".join(parts)


# ---------------------------------------------------------------------------
# Convenience function for batch scoring
# ---------------------------------------------------------------------------

def score_listings(
    listings: list[dict[str, Any]],
    user_zip: Optional[str] = None,
    max_distance: float = 40.0,
) -> list[dict[str, Any]]:
    """
    Score a list of listing dicts and return them with score data attached.
    Modifies each dict in-place by adding 'score' and 'score_breakdown' keys.
    Returns sorted list (highest score first).
    """
    scorer = ResaleScorer(
        user_zip=user_zip,
        max_acceptable_distance=max_distance,
    )

    for listing in listings:
        try:
            result = scorer.score(listing)
            listing["score"] = result.score
            listing["confidence"] = result.confidence
            listing["score_breakdown"] = result.to_dict()
        except Exception as exc:
            logger.warning(
                "Failed to score listing %s: %s",
                listing.get("listing_url", "?"),
                exc,
            )
            listing["score"] = 0.0
            listing["score_breakdown"] = {}

    return sorted(listings, key=lambda x: x.get("score", 0), reverse=True)
