"""
Opportunity notifier — Telegram alerts for high-score marketplace deals.

Configure in docker-compose.yml environment section:
  TELEGRAM_BOT_TOKEN=<your bot token from @BotFather>
  TELEGRAM_CHAT_ID=<your chat/channel ID>
  ALERT_SCORE_THRESHOLD=70   (optional, default 70)

Deduplicates via /app/storage/alerted_ids.json (persisted across restarts).
"""
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
ALERT_SCORE_THRESHOLD: float = float(os.environ.get("ALERT_SCORE_THRESHOLD", "70"))
STORAGE_PATH: Path = Path(os.environ.get("STORAGE_PATH", "/app/storage"))
ALERTED_IDS_FILE: Path = STORAGE_PATH / "alerted_ids.json"


def _get(listing: dict, field: str, default=None):
    """Read a field from listing top level or fall back to score_breakdown."""
    v = listing.get(field)
    if v is None:
        v = (listing.get("score_breakdown") or {}).get(field, default)
    return v if v is not None else default


def is_opportunity(listing: dict) -> bool:
    """Return True if listing meets alert criteria based on score, confidence, and travel tier."""
    score = listing.get("score") or 0
    confidence = _get(listing, "confidence", 0.7)
    travel_tier = _get(listing, "travel_tier", "unknown")
    profit_low = _get(listing, "estimated_profit_low") or 0
    category = (listing.get("category") or "").lower()

    if score < ALERT_SCORE_THRESHOLD or confidence < 0.6:
        return False

    if travel_tier == "local":
        return True
    elif travel_tier == "stretch":
        return profit_low >= 500
    elif travel_tier == "far":
        far_profit_ok = profit_low >= float(os.environ.get("FAR_OVERRIDE_PROFIT", "2500"))
        far_cat_ok = any(c in category for c in ["equipment", "vehicle", "motorcycle", "truck", "heavy"])
        return far_profit_ok and far_cat_ok
    else:  # unknown distance
        return score >= 75


def alert_reason(listing: dict) -> str:
    """Return a human-readable string explaining why this listing qualifies."""
    travel_tier = _get(listing, "travel_tier", "unknown")
    score = listing.get("score") or 0
    profit_low = _get(listing, "estimated_profit_low") or 0

    if travel_tier == "local":
        return "local high-confidence flip" if score >= 80 else "local deal"
    elif travel_tier == "stretch":
        return "stretch zone — strong margin" if profit_low >= 1000 else "stretch zone — acceptable margin"
    elif travel_tier == "far":
        return "far distance overridden by exceptional profit"
    else:
        return "unknown location — high score"


def _load_alerted_ids() -> set:
    if ALERTED_IDS_FILE.exists():
        try:
            with open(ALERTED_IDS_FILE) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def _save_alerted_ids(ids: set) -> None:
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    with open(ALERTED_IDS_FILE, "w") as f:
        json.dump(sorted(ids), f, indent=2)


def format_alert(listing: dict) -> str:
    """Format listing as a Telegram HTML message with geo context."""
    score = listing.get("score") or 0
    title = listing.get("title", "Unknown")
    price = listing.get("price")
    price_str = f"${price:,.0f}" if price is not None else "Price N/A"
    url = listing.get("listing_url", "")

    breakdown = listing.get("score_breakdown") or {}
    keywords = breakdown.get("matched_keywords") or listing.get("keywords") or []
    explanation = breakdown.get("explanation", "")

    travel_tier = _get(listing, "travel_tier", "unknown")
    distance_miles = _get(listing, "distance_miles")
    effective_profit = _get(listing, "effective_profit_after_travel")
    reason = alert_reason(listing)

    tier_emoji = {
        "local": "🟢",
        "stretch": "🟡",
        "far": "🔴",
    }.get(travel_tier, "⚪")

    if keywords:
        deal_signal = f"Keywords: {', '.join(keywords)}"
    elif explanation:
        deal_signal = explanation.split(".")[0]
    else:
        deal_signal = "High deal score"

    lines = [
        f"🔥 <b>Deal Alert — Score {score:.0f}/150</b>",
        f"<b>{title}</b>",
        f"💰 {price_str}",
        f"{tier_emoji} {travel_tier.capitalize()} — {reason}",
        f"📊 {deal_signal}",
    ]
    if distance_miles is not None:
        lines.append(f"📍 {distance_miles:.0f} miles away")
    if effective_profit is not None:
        profit_str = f"${effective_profit:,.0f}" if effective_profit >= 0 else f"-${abs(effective_profit):,.0f}"
        lines.append(f"💵 Profit after travel: {profit_str}")
    if url:
        lines.append(f'🔗 <a href="{url}">View Listing</a>')
    return "\n".join(lines)


def _send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(
            "Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            "in docker-compose.yml environment section"
        )
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        logger.error("Telegram HTTP error %s: %s", e.code, e.read().decode())
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
    return False


def maybe_alert(listing: dict, listing_id: str) -> bool:
    """Send alert if opportunity criteria met and listing not already alerted.

    Returns True if alert was sent.
    """
    if not is_opportunity(listing):
        return False
    alerted = _load_alerted_ids()
    if listing_id in alerted:
        return False
    sent = _send_telegram(format_alert(listing))
    if sent:
        alerted.add(listing_id)
        _save_alerted_ids(alerted)
        logger.info("Alert sent for listing %s (score=%.1f)", listing_id, listing.get("score", 0))
    return sent


def send_test_alert(listing: dict) -> bool:
    """Send alert bypassing deduplication — for /api/opportunities/alert-test."""
    return _send_telegram(format_alert(listing))
