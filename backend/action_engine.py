"""
Action Engine — converts scored listings into ranked operator actions.
Answers: "What should I do RIGHT NOW to make money?"
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

TIME_TO_CASH: dict[str, tuple[int, int]] = {
    "tools":           (1, 3),
    "electronics":     (1, 5),
    "motorcycles":     (3, 10),
    "vehicles":        (3, 14),
    "cars":            (3, 14),
    "trucks":          (3, 14),
    "equipment":       (7, 21),
    "heavy_equipment": (14, 30),
    "bulk":            (7, 30),
    "general":         (5, 21),
    "unknown":         (7, 21),
}

_HEAVY_TRANSPORT = {"excavator", "backhoe", "crane", "semi", "tractor"}
_BULK_KEYWORDS   = {"lot of", "bundle", "misc", "assorted", "various"}


def _get(listing: dict, field: str, default=None):
    v = listing.get(field)
    if v is None:
        v = (listing.get("score_breakdown") or {}).get(field, default)
    return v if v is not None else default


def _resolve_ttc_category(listing: dict) -> str:
    cat   = (listing.get("category") or "").lower().strip()
    title = (listing.get("title") or "").lower()

    if any(k in title for k in ("excavator", "backhoe", "crane", "bulldozer")):
        return "heavy_equipment"
    if cat in TIME_TO_CASH:
        return cat
    for key in TIME_TO_CASH:
        if key in cat:
            return key
    if any(k in title for k in ("motorcycle", "kawasaki", "harley", "yamaha moto")):
        return "motorcycles"
    if any(k in title for k in ("truck", "pickup", "f-150", "f150", "silverado", "ram ")):
        return "trucks"
    if any(k in title for k in ("car", "sedan", "suv", "jeep", "honda", "toyota", "ford", "chevy")):
        return "vehicles"
    if any(k in title for k in ("iphone", "macbook", "laptop", "ps5", "xbox", "gaming", "gpu", "drone")):
        return "electronics"
    if any(k in title for k in ("tool", "dewalt", "milwaukee", "makita", "snap-on", "snap on", "welder", "compressor")):
        return "tools"
    if any(k in title for k in ("tractor", "bobcat", "skid steer", "forklift")):
        return "equipment"
    return "general"


def _profit_score(effective_profit: Optional[float]) -> float:
    if not effective_profit or effective_profit <= 0:
        return 0.0
    if effective_profit >= 2500:
        return 35.0
    if effective_profit >= 1000:
        return 30.0 + (effective_profit - 1000) / 1500 * 5
    if effective_profit >= 500:
        return 20.0 + (effective_profit - 500) / 500 * 10
    return effective_profit / 500 * 20


def _profit_per_day_score(ppd: float) -> float:
    if ppd >= 200:
        return 20.0
    if ppd >= 50:
        return 15.0
    if ppd >= 10:
        return 10.0
    return 5.0


def _distance_score(travel_tier: str) -> float:
    return {"local": 15.0, "stretch": 10.0, "far": 3.0}.get(travel_tier, 8.0)


def _urgency_score(listing: dict) -> float:
    try:
        from modules.marketplace_scraper.scorer import URGENCY_KEYWORDS
    except ImportError:
        URGENCY_KEYWORDS = [
            "must sell", "moving", "estate sale", "divorce", "need cash",
            "asap", "quick sale", "fire sale", "obo", "price drop",
        ]
    title = (listing.get("title") or "").lower()
    desc  = (listing.get("description") or "").lower()
    text  = f"{title} {desc}"
    found = sum(1 for kw in URGENCY_KEYWORDS if kw in text)
    if found == 0:
        return 0.0
    if found == 1:
        return 5.0
    return 10.0


def _friction(listing: dict) -> tuple[float, list[str]]:
    title = (listing.get("title") or "").lower()
    desc  = (listing.get("description") or "").lower()
    text  = f"{title} {desc}"
    price = listing.get("price")

    total: float = 0.0
    reasons: list[str] = []

    if any(k in text for k in _HEAVY_TRANSPORT):
        total += 10
        reasons.append("requires heavy transport")

    if len((listing.get("title") or "").split()) < 3:
        total += 8
        reasons.append("vague title")

    if price is None:
        total += 10
        reasons.append("price unknown")

    if any(k in text for k in _BULK_KEYWORDS):
        total += 6
        reasons.append("bulk complexity")

    return min(total, 30.0), reasons


def _reason_to_act(listing: dict, effective_profit: float, travel_tier: str,
                   confidence: float, ppd: float) -> str:
    signals: list[str] = []

    if effective_profit and effective_profit > 0:
        signals.append(f"~${effective_profit:,.0f} profit")

    if travel_tier == "local":
        signals.append("local pickup")
    elif travel_tier == "stretch":
        signals.append("drivable stretch")

    if confidence >= 0.85:
        signals.append("high confidence")

    if ppd >= 100:
        signals.append(f"${ppd:.0f}/day return")
    elif ppd >= 50:
        signals.append(f"${ppd:.0f}/day")

    title_lower = (listing.get("title") or "").lower()
    if any(k in title_lower for k in ("must sell", "moving", "estate", "divorce", "quick sale", "need cash")):
        signals.append("motivated seller")

    return ", ".join(signals[:2]) if signals else "solid deal score"


def _why_ranked_here(rank: int, action_score: float, effective_profit: float,
                     travel_tier: str) -> str:
    if rank == 1:
        return (
            f"Highest combined action score ({action_score:.0f}/100) — "
            "best balance of profit, speed, and accessibility right now."
        )
    if rank == 2:
        if travel_tier == "local":
            return "Strong local option — ranked #2 for easy pickup vs. higher-profit but harder #1."
        return f"Second-best action score ({action_score:.0f}/100) — solid margin with manageable friction."
    tier_note = {"local": "local reach", "stretch": "stretch haul", "far": "long haul"}.get(
        travel_tier, "decent reach"
    )
    profit_str = f"${effective_profit:,.0f}" if effective_profit else "unknown"
    return f"Rounds out your Top 3 — {tier_note} with {profit_str} est. upside if you move today."


def compute_action_score(listing: dict) -> dict:
    """Compute action score and supporting metadata for a single listing."""
    effective_profit = float(_get(listing, "effective_profit_after_travel") or 0)
    confidence       = float(_get(listing, "confidence") or 0.7)
    travel_tier      = _get(listing, "travel_tier", "unknown") or "unknown"

    ttc_cat = _resolve_ttc_category(listing)
    min_days, max_days = TIME_TO_CASH.get(ttc_cat, (7, 21))
    time_to_cash_days  = (min_days + max_days) / 2.0

    ppd = effective_profit / time_to_cash_days if time_to_cash_days > 0 else 0.0

    raw = (
        _profit_score(effective_profit)
        + confidence * 20
        + _profit_per_day_score(ppd)
        + _distance_score(travel_tier)
        + _urgency_score(listing)
    )

    friction_score, friction_reasons = _friction(listing)
    action_score = max(0.0, min(100.0, raw - friction_score))

    return {
        "action_score":      round(action_score, 1),
        "time_to_cash_days": time_to_cash_days,
        "profit_per_day":    round(ppd, 2),
        "friction_score":    round(friction_score, 1),
        "friction_reasons":  friction_reasons,
        "reason_to_act":     _reason_to_act(listing, effective_profit, travel_tier, confidence, ppd),
    }


def rank_top_actions(scored_opportunities: list[dict], top_n: int = 3) -> tuple[list[dict], int]:
    """Score + rank all opportunities. Returns (top_n_list, suppressed_count)."""
    scored: list[dict] = []
    for opp in scored_opportunities:
        try:
            action = compute_action_score(opp)
            scored.append({**opp, **action})
        except Exception as exc:
            logger.warning("action score failed for %s: %s", opp.get("title", "?"), exc)

    scored.sort(key=lambda x: x.get("action_score", 0), reverse=True)
    top        = scored[:top_n]
    suppressed = max(0, len(scored) - top_n)

    for i, item in enumerate(top, 1):
        effective_profit = float(_get(item, "effective_profit_after_travel") or 0)
        item["rank"]            = i
        item["why_ranked_here"] = _why_ranked_here(
            i, item["action_score"], effective_profit, item.get("travel_tier", "unknown")
        )

    return top, suppressed
