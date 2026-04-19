"""
Operator Console — builds a unified deal briefing dict from MongoDB scored_opportunities.
Runs analyst, CFO, and CoS pipeline over top deals and persists output.
"""
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

import action_engine

logger = logging.getLogger(__name__)

CONSOLE_JSON_PATH = Path(os.environ.get("STORAGE_PATH", "/app/storage")) / "operator_console.json"
SCRAPE_LOG_PATH   = Path(os.environ.get("STORAGE_PATH", "/app/storage")) / "scrape_log.json"


def _analyst_pass(listing: dict) -> tuple[str, str]:
    action_score = float(listing.get("action_score") or 0)
    confidence   = float(listing.get("confidence")   or 0)
    if action_score >= 45 and confidence >= 0.55:
        return "pass", f"score={action_score:.0f}, conf={confidence:.2f}"
    reasons = []
    if action_score < 45:
        reasons.append(f"action_score {action_score:.0f}<45")
    if confidence < 0.55:
        reasons.append(f"confidence {confidence:.2f}<0.55")
    return "fail", "; ".join(reasons)


def _cfo_pass(listing: dict) -> tuple[str, str]:
    price  = listing.get("price") or 0
    profit = listing.get("estimated_profit_low") or listing.get("effective_profit_after_travel") or 0
    try:
        price  = float(price)
        profit = float(profit)
    except (TypeError, ValueError):
        return "rejected", "missing price or profit data"

    if price <= 0:
        return "rejected", "no price"
    if price > 5000:
        return "rejected", f"price ${price:,.0f} exceeds $5,000 cap"
    margin = (profit - price) / price if price > 0 else 0
    if margin > 0.30:
        return "approved", f"{margin*100:.0f}% margin"
    return "rejected", f"margin {margin*100:.0f}% below 30% threshold"


def _cos_action(listing: dict, rank: int) -> str:
    title    = listing.get("title") or "this item"
    tier     = listing.get("travel_tier") or "unknown"
    price    = listing.get("price") or 0
    distance = listing.get("distance_miles")

    offer = round(price * 0.90) if price else None
    offer_str = f"Offer ${offer:,}." if offer else "Negotiate price."

    pickup_str = ""
    if tier == "local":
        pickup_str = "Pick up today."
    elif tier == "stretch":
        dist_str = f" ({distance:.0f}mi)" if distance else ""
        pickup_str = f"Schedule trip{dist_str} this weekend."
    elif tier == "far":
        pickup_str = "Evaluate transport cost before committing."

    return f"Contact seller. {offer_str} {pickup_str}".strip()


def _last_scrape_ts(scrape_log_path: Path) -> str:
    try:
        if not scrape_log_path.exists():
            return None
        data = json.loads(scrape_log_path.read_text())
        if isinstance(data, list) and data:
            return data[-1].get("ts") or data[-1].get("started_at")
        if isinstance(data, dict):
            runs = data.get("runs") or []
            if runs:
                return runs[-1].get("ts") or runs[-1].get("started_at")
    except Exception:
        pass
    return None


def _active_sources(mongo_col) -> list:
    try:
        sources = mongo_col.distinct("source")
        return [s for s in sources if s]
    except Exception:
        return ["craigslist", "govplanet", "publicsurplus"]


def build_console_data(mongo_col, scrape_log_path: Path = None) -> dict:
    """Pull top deals from MongoDB, run analyst/CFO/CoS pipeline, return console dict."""
    from events import emit

    if scrape_log_path is None:
        scrape_log_path = SCRAPE_LOG_PATH

    try:
        from pymongo import DESCENDING
        top_cursor = (
            mongo_col.find({})
            .sort([("action_score", DESCENDING), ("score", DESCENDING)])
            .limit(50)
        )
        raw_docs = list(top_cursor)
    except Exception as exc:
        logger.error("operator_console: MongoDB query failed: %s", exc)
        raw_docs = []

    try:
        total_tracked = mongo_col.count_documents({})
    except Exception:
        total_tracked = len(raw_docs)

    suppressed_reasons = {"low_action_score": 0, "cfo_rejected": 0, "analyst_fail": 0}
    top_deals = []
    cos_rank_counter = 0

    for doc in raw_docs:
        try:
            listing_id = str(doc.get("listing_id") or doc.get("_id") or "unknown")
            clean = {k: v for k, v in doc.items() if k != "_id"}

            # Fill effective_profit fallback before scoring
            if not clean.get("effective_profit_after_travel"):
                clean["effective_profit_after_travel"] = clean.get("estimated_profit_low") or 0

            # Compute/refresh action score
            try:
                action_fields = action_engine.compute_action_score(clean)
                clean.update(action_fields)
            except Exception:
                pass

            action_score = float(clean.get("action_score") or 0)
            confidence   = float(clean.get("confidence")   or 0)
            profit       = float(clean.get("estimated_profit_low") or 0)
            price        = float(clean.get("price") or 0)
            eff_profit   = float(clean.get("effective_profit_after_travel") or profit)
            ppd          = float(clean.get("profit_per_day") or 0)

            # Analyst gate
            analyst_verdict, analyst_reason = _analyst_pass(clean)
            if analyst_verdict == "fail":
                suppressed_reasons["analyst_fail"] += 1
                emit("analyst_decision", "analyst", f"FAIL: {clean.get('title','?')[:40]}",
                     analyst_reason, severity="info",
                     metadata={"listing_id": listing_id, "verdict": "fail"})
                continue

            # CFO gate
            cfo_decision, cfo_rationale = _cfo_pass(clean)
            emit("cfo_decision", "cfo", f"CFO {cfo_decision.upper()}: {clean.get('title','?')[:40]}",
                 cfo_rationale, severity="info" if cfo_decision == "approved" else "warn",
                 metadata={"listing_id": listing_id, "decision": cfo_decision})

            if cfo_decision == "rejected":
                suppressed_reasons["cfo_rejected"] += 1
                continue

            # Passed both gates — assign CoS rank
            cos_rank_counter += 1
            cos_action_str = _cos_action(clean, cos_rank_counter)

            emit("cos_decision", "cos", f"CoS #{cos_rank_counter}: {clean.get('title','?')[:40]}",
                 cos_action_str, severity="info",
                 metadata={"listing_id": listing_id, "rank": cos_rank_counter})

            top_deals.append({
                "id":                            listing_id,
                "title":                         clean.get("title") or "Untitled",
                "source":                        clean.get("source") or "unknown",
                "price":                         price,
                "estimated_profit":              profit,
                "effective_profit_after_travel": eff_profit,
                "profit_per_day":                ppd,
                "distance_miles":                clean.get("distance_miles"),
                "travel_tier":                   clean.get("travel_tier") or "unknown",
                "score":                         float(clean.get("score") or 0),
                "action_score":                  action_score,
                "confidence":                    confidence,
                "risk_flag":                     clean.get("risk_flag") or "medium",
                "analyst_verdict":               analyst_verdict,
                "analyst_reason":                analyst_reason,
                "cfo_decision":                  cfo_decision,
                "cfo_rationale":                 cfo_rationale,
                "cos_action":                    cos_action_str,
                "cos_rank":                      cos_rank_counter if cos_rank_counter <= 3 else None,
                "listing_url":                   clean.get("listing_url") or clean.get("url") or "",
                "state":                         clean.get("state") or "new",
            })

            if len(top_deals) >= 10:
                break

        except Exception as exc:
            logger.warning("operator_console: skipping doc: %s", exc)
            continue

    suppressed_count = len(raw_docs) - len(top_deals)

    console = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "system_status": {
            "sources_active": _active_sources(mongo_col),
            "total_tracked":  total_tracked,
            "last_scrape":    _last_scrape_ts(scrape_log_path),
        },
        "top_deals":          top_deals,
        "suppressed_count":   max(0, suppressed_count),
        "suppressed_reasons": suppressed_reasons,
    }

    try:
        CONSOLE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONSOLE_JSON_PATH.write_text(json.dumps(console, indent=2))
    except Exception as exc:
        logger.warning("operator_console: could not write JSON: %s", exc)

    emit("top_deals_updated", "console", "Console refreshed",
         f"{len(top_deals)} deals passed pipeline, {max(0, suppressed_count)} suppressed",
         metadata={"deal_count": len(top_deals), "suppressed": max(0, suppressed_count)})

    return console
