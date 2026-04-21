"""
Contact Draft Generator — generates ready-to-send buyer messages for high-score deals.
Saves to /app/operator-data/drafts/. No auto-sending.
"""
import json
import hashlib
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DRAFTS_PATH = Path(os.environ.get("DRAFTS_PATH", "/app/operator-data/drafts"))
DRAFTS_INDEX = DRAFTS_PATH / "drafts_index.json"
DRAFT_SCORE_THRESHOLD = 80

_CASUAL_SOURCES = {"craigslist", "facebook"}


def _variate(listing_id: str, n: int) -> int:
    """Deterministic variation 0..n-1 keyed on listing_id so same listing always matches."""
    return int(hashlib.md5(listing_id.encode()).hexdigest(), 16) % n


def _offer(price: float) -> int:
    """85% of asking, rounded to nearest $5."""
    return int(round(price * 0.85 / 5) * 5)


def _casual_draft(listing: dict, lid: str) -> str:
    price = float(listing.get("price") or 0)
    v = _variate(lid, 4)

    openers = [
        "Hey, is this still available?",
        "Hi! Still have this?",
        "Hey — is this still up for grabs?",
        "Hi there, still available?",
    ]
    pickups = [
        "I can pick up today.",
        "I can come get it today or tomorrow.",
        "Available to pick up same day.",
        "I can be there today.",
    ]
    if price:
        o = _offer(price)
        offers = [
            f"Would you take ${o:,}?",
            f"Would you do ${o:,}?",
            f"Any chance you'd take ${o:,}?",
            f"Best you can do on ${o:,}?",
        ]
    else:
        offers = [
            "What's the best you can do?",
            "What's your lowest?",
            "Any wiggle room on price?",
            "Would you take less?",
        ]

    return f"{openers[v]} {pickups[v]} {offers[v]}"


def _formal_draft(listing: dict, lid: str) -> str:
    title = (listing.get("title") or "the listed item").strip()
    price = float(listing.get("price") or 0)
    v = _variate(lid, 3)

    if price:
        o = _offer(price)
        templates = [
            f"Hello, I'm interested in the {title}. Would you accept ${o:,}? I can arrange pickup promptly.",
            f"Good day — I'd like to inquire about the {title} listed at ${price:,.0f}. Would you consider ${o:,}? I can coordinate pickup at your earliest convenience.",
            f"Hi, I'm interested in purchasing the {title}. I can offer ${o:,} and arrange pickup quickly. Please let me know.",
        ]
    else:
        templates = [
            f"Hello, I'm interested in the {title}. Could you share your best price? I can arrange pickup promptly.",
            f"Good day — I'd like to inquire about the {title}. What's the best price available? I can coordinate pickup at your convenience.",
            f"Hi, I'm interested in the {title}. Please let me know your best price and pickup availability.",
        ]

    return templates[v]


def generate_contact_draft(listing: dict) -> str:
    """Generate a ready-to-send contact message for a listing."""
    lid = str(
        listing.get("listing_id") or listing.get("id") or listing.get("_id") or "unknown"
    )
    source = (listing.get("source") or "").lower()
    if source in _CASUAL_SOURCES:
        return _casual_draft(listing, lid)
    return _formal_draft(listing, lid)


def _load_index() -> dict:
    if not DRAFTS_INDEX.exists():
        return {}
    try:
        entries = json.loads(DRAFTS_INDEX.read_text())
        return {e["listing_id"]: e for e in entries if "listing_id" in e}
    except Exception:
        return {}


def _save_index(index: dict) -> None:
    DRAFTS_PATH.mkdir(parents=True, exist_ok=True)
    entries = sorted(index.values(), key=lambda x: x.get("created_at", ""), reverse=True)
    DRAFTS_INDEX.write_text(json.dumps(entries, indent=2))


def save_draft(listing: dict, listing_id: str) -> dict | None:
    """
    Persist a contact draft for a qualifying listing.
    Returns the index entry dict, or None if already drafted.
    """
    index = _load_index()
    if listing_id in index:
        return None

    draft_text = generate_contact_draft({**listing, "listing_id": listing_id})

    DRAFTS_PATH.mkdir(parents=True, exist_ok=True)
    draft_file = DRAFTS_PATH / f"contact_{listing_id}.txt"
    try:
        draft_file.write_text(draft_text)
    except Exception as exc:
        logger.warning("contact_drafter: could not write draft file: %s", exc)
        return None

    price = float(listing.get("price") or 0)
    offer = float(_offer(price)) if price else 0.0

    entry = {
        "listing_id":   listing_id,
        "title":        (listing.get("title") or "Untitled")[:80],
        "source":       listing.get("source") or "unknown",
        "price":        price,
        "offer_price":  offer,
        "score":        float(listing.get("score") or 0),
        "listing_url":  listing.get("listing_url") or listing.get("url") or "",
        "draft_preview": draft_text[:100],
        "draft_file":   str(draft_file),
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "status":       "draft",
    }
    index[listing_id] = entry
    _save_index(index)

    # Create approval request so operator can authorize before sending
    try:
        from approvals import create_approval, ACTION_SEND_CONTACT
        create_approval(
            action_type=ACTION_SEND_CONTACT,
            title=f"Send offer to: {entry['title']}",
            payload={
                "listing_id":  listing_id,
                "listing_url": entry["listing_url"],
                "source":      entry["source"],
                "price":       price,
                "offer_price": offer,
                "draft_text":  draft_text,
            },
            owner="contact_drafter",
        )
    except Exception as exc:
        logger.warning("contact_drafter: could not create approval: %s", exc)

    return entry


def get_drafts(status: str = "draft") -> list:
    """Return all drafts with given status, newest first, with full text loaded."""
    index = _load_index()
    results = []
    for entry in sorted(index.values(), key=lambda x: x.get("created_at", ""), reverse=True):
        if entry.get("status") != status:
            continue
        draft_text = entry.get("draft_preview", "")
        fpath = entry.get("draft_file")
        if fpath:
            try:
                draft_text = Path(fpath).read_text().strip()
            except Exception:
                pass
        results.append({**entry, "draft_text": draft_text})
    return results


def mark_draft(listing_id: str, status: str) -> bool:
    """Update draft status. Returns True if found."""
    index = _load_index()
    if listing_id not in index:
        return False
    index[listing_id]["status"] = status
    _save_index(index)
    return True
