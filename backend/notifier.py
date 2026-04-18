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

def is_opportunity(listing: dict) -> bool:
    """Return True if listing meets alert criteria: score >= threshold and confidence >= 0.6."""
    score = listing.get("score") or 0
    confidence = listing.get("confidence", 0.7)
    return score >= ALERT_SCORE_THRESHOLD and confidence >= 0.6


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
    """Format listing as a Telegram HTML message."""
    score = listing.get("score") or 0
    title = listing.get("title", "Unknown")
    price = listing.get("price")
    price_str = f"${price:,.0f}" if price is not None else "Price N/A"
    url = listing.get("listing_url", "")

    breakdown = listing.get("score_breakdown") or {}
    keywords = breakdown.get("matched_keywords") or listing.get("keywords") or []
    explanation = breakdown.get("explanation", "")

    if keywords:
        reason = f"Keywords: {', '.join(keywords)}"
    elif explanation:
        reason = explanation.split(".")[0]
    else:
        reason = "High deal score"

    lines = [
        f"🔥 <b>Deal Alert — Score {score:.0f}/100</b>",
        f"<b>{title}</b>",
        f"💰 {price_str}",
        f"📊 {reason}",
    ]
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
