"""
Geographic distance and travel tier logic for DealScope.
All configuration is via environment variables — no code changes needed to tune.
"""
import math
import os
import re

HOME_LAT = float(os.environ.get("HOME_LAT", "26.3837"))
HOME_LON = float(os.environ.get("HOME_LON", "-98.8219"))
LOCAL_RADIUS_MILES = float(os.environ.get("LOCAL_RADIUS_MILES", "35"))
STRETCH_RADIUS_MILES = float(os.environ.get("STRETCH_RADIUS_MILES", "110"))
FAR_OVERRIDE_PROFIT = float(os.environ.get("FAR_OVERRIDE_PROFIT", "2500"))
FAR_OVERRIDE_CATEGORIES = set(
    os.environ.get(
        "FAR_OVERRIDE_CATEGORIES",
        "heavy_equipment,equipment,vehicles,motorcycles,cars+trucks",
    ).split(",")
)
LOCAL_FLIPS_MODE = os.environ.get("LOCAL_FLIPS_MODE", "true").lower() == "true"

TX_CITIES: dict = {
    "rio grande city": (26.3837, -98.8219),
    "south padre island": (26.1103, -97.1691),
    "port isabel": (26.0712, -97.2130),
    "corpus christi": (27.8006, -97.3964),
    "san antonio": (29.4241, -98.4936),
    "san juan": (26.1895, -98.1547),
    "mcallen": (26.2034, -98.2300),
    "harlingen": (26.1906, -97.6961),
    "brownsville": (25.9017, -97.4975),
    "weslaco": (26.1595, -97.9908),
    "edinburg": (26.3017, -98.1633),
    "mission": (26.2159, -98.3252),
    "laredo": (27.5306, -99.4803),
    "houston": (29.7604, -95.3698),
    "dallas": (32.7767, -96.7970),
    "austin": (30.2672, -97.7431),
    "pharr": (26.1945, -98.1836),
}

_FAR_CATEGORY_KEYWORDS = frozenset({"equipment", "vehicle", "motorcycle", "truck", "heavy"})


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    R = 3958.8
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))


def geocode_location(location_str: str):
    """
    Parse lat/lon from a listing location string.
    Tries: (1) "lat,lon" format, (2) TX city name lookup.
    Returns (lat, lon) tuple or None if unparseable.
    """
    if not location_str:
        return None
    s = location_str.strip()

    m = re.match(r"^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$", s)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass

    lower = s.lower()
    # Match longer city names first to avoid partial collisions
    for city in sorted(TX_CITIES, key=len, reverse=True):
        if city in lower:
            return TX_CITIES[city]

    return None


def get_travel_tier(distance_miles: float) -> str:
    """Return 'local', 'stretch', or 'far' based on configured radii."""
    if distance_miles <= LOCAL_RADIUS_MILES:
        return "local"
    if distance_miles <= STRETCH_RADIUS_MILES:
        return "stretch"
    return "far"


def get_distance_penalty(distance_miles: float, travel_tier: str, estimated_profit_low: float) -> int:
    """
    Return negative int score penalty for distance.
    LOCAL_FLIPS_MODE applies stricter penalties for non-local items.
    """
    if travel_tier == "local":
        return 0
    if travel_tier == "stretch":
        if LOCAL_FLIPS_MODE:
            return -20 if estimated_profit_low < 1000 else -10
        return -10 if estimated_profit_low < 1000 else -5
    if travel_tier == "far":
        if LOCAL_FLIPS_MODE:
            return -50  # May be softened in score_geo if override qualifies
        return -30 if estimated_profit_low < FAR_OVERRIDE_PROFIT else -10
    return 0


def get_effective_profit(estimated_profit_low: float, distance_miles: float) -> float:
    """Subtract estimated travel cost (gas: $0.25/mile roundtrip) from profit."""
    return estimated_profit_low - (distance_miles * 2 * 0.25)


def _far_category_ok(listing: dict) -> bool:
    combined = (
        (listing.get("category") or "").lower()
        + " "
        + (listing.get("title") or "").lower()
    )
    return any(kw in combined for kw in _FAR_CATEGORY_KEYWORDS)


def score_geo(listing: dict) -> dict:
    """
    Compute geo context for a listing.
    Returns dict with: distance_miles, travel_tier, distance_penalty,
    effective_profit_after_travel, geocoded.
    Never raises — unknown location degrades gracefully to travel_tier='unknown'.
    """
    try:
        location_str = str(listing.get("location") or "")
        estimated_profit_low = float(listing.get("estimated_profit_low") or 0)

        coords = geocode_location(location_str)
        if coords is None:
            return {
                "distance_miles": None,
                "travel_tier": "unknown",
                "distance_penalty": 0,
                "effective_profit_after_travel": None,
                "geocoded": False,
            }

        lat, lon = coords
        distance_miles = haversine(HOME_LAT, HOME_LON, lat, lon)
        travel_tier = get_travel_tier(distance_miles)
        distance_penalty = get_distance_penalty(distance_miles, travel_tier, estimated_profit_low)

        # In LOCAL_FLIPS_MODE, the default far penalty is -50.
        # Soften to -10 when both profit threshold AND category qualify.
        if (
            LOCAL_FLIPS_MODE
            and travel_tier == "far"
            and estimated_profit_low >= FAR_OVERRIDE_PROFIT
            and _far_category_ok(listing)
        ):
            distance_penalty = -10

        effective_profit = get_effective_profit(estimated_profit_low, distance_miles)

        return {
            "distance_miles": round(distance_miles, 1),
            "travel_tier": travel_tier,
            "distance_penalty": distance_penalty,
            "effective_profit_after_travel": round(effective_profit, 2),
            "geocoded": True,
        }
    except Exception:
        return {
            "distance_miles": None,
            "travel_tier": "unknown",
            "distance_penalty": 0,
            "effective_profit_after_travel": None,
            "geocoded": False,
        }
