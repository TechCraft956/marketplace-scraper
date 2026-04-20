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
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
ALERT_SCORE_THRESHOLD: float = float(os.environ.get("ALERT_SCORE_THRESHOLD", "70"))
DEFAULT_MORE_DEALS_LIMIT: int = int(os.environ.get("TOP_ACTIONS_MORE_LIMIT", "12"))
STORAGE_PATH: Path = Path(os.environ.get("STORAGE_PATH", "/app/storage"))
ALERTED_IDS_FILE: Path = STORAGE_PATH / "alerted_ids.json"
TOP3_STATE_FILE: Path = STORAGE_PATH / "top3_alerted_ids.json"

# Per-listing maybe_alert only fires at this action_score threshold (truly exceptional)
EXCEPTIONAL_ACTION_SCORE: float = 90.0
# Minimum seconds between top-action briefings
TOP3_COOLDOWN_SECS: int = 3600
TOP_ACTIONS_BRIEF_LIMIT: int = int(os.environ.get("TOP_ACTIONS_BRIEF_LIMIT", "5"))


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


def _load_top3_state() -> dict:
    if TOP3_STATE_FILE.exists():
        try:
            with open(TOP3_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_sent": 0.0, "alerted_ids": []}


def _save_top3_state(state: dict) -> None:
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    with open(TOP3_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


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


def _telegram_api(method: str, payload: dict) -> tuple[bool, dict]:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(
            "Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            "in docker-compose.yml environment section"
        )
        return False, {"description": "telegram_not_configured"}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode() or "{}")
            return resp.status == 200 and bool(data.get("ok", True)), data
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("Telegram HTTP error %s: %s", e.code, body)
        try:
            return False, json.loads(body)
        except Exception:
            return False, {"description": body}
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False, {"description": str(e)}


def _send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(
            "Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            "in docker-compose.yml environment section"
        )
        return False

    ok, _ = _telegram_api("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })
    return ok


def maybe_alert(listing: dict, listing_id: str, action_score: float = 0.0) -> bool:
    """Send per-listing alert only for truly exceptional deals (action_score >= 90).

    Returns True if alert was sent.
    """
    if action_score < EXCEPTIONAL_ACTION_SCORE:
        return False
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


def _format_top3_move(rank: int, item: dict) -> str:
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "🏅")
    rank_label = {1: "#1 MOVE", 2: "#2 MOVE", 3: "#3 MOVE"}.get(rank, f"#{rank} MOVE")

    title = item.get("title", "Unknown")
    price = item.get("price")
    price_str = f"${price:,.0f}" if price is not None else "N/A"

    effective_profit = _get(item, "effective_profit_after_travel") or 0
    profit_str = f"${effective_profit:,.0f}" if effective_profit else "N/A"

    ppd = item.get("profit_per_day") or 0
    ppd_str = f"${ppd:,.0f}/day" if ppd else "N/A"

    travel_tier = _get(item, "travel_tier", "unknown") or "unknown"
    tier_emoji = {"local": "🟢", "stretch": "🟡", "far": "🔴"}.get(travel_tier, "⚪")
    tier_label = travel_tier.capitalize()

    confidence = _get(item, "confidence") or 0
    conf_str = f"{confidence:.2f}"

    reason = item.get("reason_to_act", "")
    url = item.get("listing_url", "")

    lines = [
        f"{medal} <b>{rank_label}</b>",
        f"<b>{title}</b>",
        f"💰 {price_str} → est. flip {profit_str} (after travel)",
        f"📈 {ppd_str} | {tier_emoji} {tier_label} | Conf: {conf_str}",
        f"✅ {reason}",
    ]
    if url:
        lines.append(f'🔗 <a href="{url}">View Listing</a>')
    return "\n".join(lines)


def format_top3_briefing(top_actions: list[dict], suppressed_count: int, more_label: str = "Show other deals") -> str:
    """Format the combined operator briefing message."""
    lines = ["🏆 <b>OPERATOR BRIEFING — Top Moves Right Now</b>", ""]
    for item in top_actions:
        rank = item.get("rank", 0)
        lines.append(_format_top3_move(rank, item))
        lines.append("")
    if suppressed_count > 0:
        lines.append(f"⚡ {suppressed_count} other deals tracked. Reply \"{more_label}\" to expand the list.")
    return "\n".join(lines)


def maybe_alert_top3(top_actions: list[dict], suppressed_count: int = 0) -> bool:
    """Fire ONE combined Telegram briefing when the top action set refreshes with new listings.

    Deduplicates by listing_id. Rate-limited to once per hour.
    Returns True if message was sent.
    """
    if not top_actions:
        return False

    top_actions = top_actions[:TOP_ACTIONS_BRIEF_LIMIT]

    state = _load_top3_state()
    alerted_ids: set[str] = set(state.get("alerted_ids", []))
    last_sent: float = float(state.get("last_sent", 0))

    # Extract IDs from current top 3
    current_ids: list[str] = []
    for item in top_actions:
        lid = (
            item.get("listing_id")
            or item.get("id")
            or str(item.get("_id", ""))
        )
        if lid:
            current_ids.append(lid)

    # Check if at least one new listing
    has_new = any(lid not in alerted_ids for lid in current_ids if lid)
    if not has_new:
        logger.info("top3 briefing: no new listings, suppressing")
        return False

    # Enforce rate limit
    now = time.time()
    if now - last_sent < TOP3_COOLDOWN_SECS:
        remaining = int(TOP3_COOLDOWN_SECS - (now - last_sent))
        logger.info("top3 briefing: rate-limited, %ds remaining", remaining)
        return False

    message = format_top3_briefing(top_actions, suppressed_count)
    sent = _send_telegram(message)

    if sent and suppressed_count > 0:
        _telegram_api("sendMessage", {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": "Tap below for more deals.",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "Show other deals", "callback_data": f"more_deals:{DEFAULT_MORE_DEALS_LIMIT}"}
                ]]
            }
        })

    if sent:
        alerted_ids.update(lid for lid in current_ids if lid)
        _save_top3_state({"last_sent": now, "alerted_ids": sorted(alerted_ids)})
        logger.info("top3 briefing sent (ids: %s)", current_ids)
    return sent
