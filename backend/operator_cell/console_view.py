#!/usr/bin/env python3
"""
Pineapple Operator Console — CLI view.
Hits /operator/console and prints a terminal briefing.
"""
import sys
import json
import urllib.request
from datetime import datetime

BASE_URL = "http://localhost:8000"

MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
TIER_ICON = {"local": "🟢", "stretch": "🟡", "far": "🔴", "unknown": "⚪"}
RISK_ICON = {"low": "LOW", "medium": "MED", "high": "HIGH"}


def _fetch_console(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"ERROR: Could not reach {url}: {exc}", file=sys.stderr)
        sys.exit(1)


def _fmt_ts(ts_str: str) -> str:
    if not ts_str:
        return "never"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_str[:16]


def _print_deal(deal: dict, idx: int) -> None:
    rank      = deal.get("cos_rank") or idx
    medal     = MEDAL.get(rank, f"#{rank}")
    title     = deal.get("title") or "Untitled"[:50]
    source    = deal.get("source") or "?"
    price     = deal.get("price") or 0
    profit    = deal.get("effective_profit_after_travel") or deal.get("estimated_profit") or 0
    ppd       = deal.get("profit_per_day") or 0
    dist      = deal.get("distance_miles")
    tier      = deal.get("travel_tier") or "unknown"
    score     = deal.get("action_score") or deal.get("score") or 0
    conf      = deal.get("confidence") or 0
    risk      = deal.get("risk_flag") or "medium"
    cfo       = deal.get("cfo_decision") or "?"
    cfo_rat   = deal.get("cfo_rationale") or ""
    cos_act   = deal.get("cos_action") or ""

    tier_icon = TIER_ICON.get(tier, "⚪")
    dist_str  = f"{dist:.0f}mi" if dist is not None else "?mi"
    risk_str  = RISK_ICON.get(risk, risk.upper())

    cfo_icon = "✅" if cfo == "approved" else "❌"

    print(f"\n{medal} {title} ({source})")
    print(f"   💰 ${price:,.0f} → ${profit:,.0f} profit | ${ppd:.0f}/day | {tier_icon} {dist_str} {tier}")
    print(f"   📊 Score:{score:.0f} | Conf:{conf:.2f} | Risk:{risk_str}")
    print(f"   {cfo_icon} CFO: {cfo.upper()} — {cfo_rat}")
    if cos_act:
        print(f"   👉 {cos_act}")


def main() -> None:
    url = f"{BASE_URL}/operator/console"
    data = _fetch_console(url)

    status       = data.get("system_status") or {}
    generated_at = _fmt_ts(data.get("generated_at") or "")
    total        = status.get("total_tracked") or 0
    sources      = status.get("sources_active") or []
    last_scrape  = _fmt_ts(status.get("last_scrape") or "")
    top_deals    = data.get("top_deals") or []
    suppressed   = data.get("suppressed_count") or 0
    sup_reasons  = data.get("suppressed_reasons") or {}

    width = 48
    border = "═" * width
    print(f"\n{border}")
    print("🍍 PINEAPPLE OPERATOR CONSOLE")
    print(f"{generated_at} | {total} tracked | {len(sources)} sources")
    print(border)

    if not top_deals:
        print("\n  No deals passed the pipeline right now.")
    else:
        for i, deal in enumerate(top_deals, 1):
            _print_deal(deal, i)

    print(f"\n{border}")
    if suppressed:
        print(f"⛔ {suppressed} suppressed", end="")
        parts = []
        if sup_reasons.get("analyst_fail"):
            parts.append(f"{sup_reasons['analyst_fail']} analyst fail")
        if sup_reasons.get("cfo_rejected"):
            parts.append(f"{sup_reasons['cfo_rejected']} CFO rejected")
        if sup_reasons.get("low_action_score"):
            parts.append(f"{sup_reasons['low_action_score']} low score")
        if parts:
            print(f" ({', '.join(parts)})", end="")
        print()
    print(f"Last scrape: {last_scrape}")
    print(border + "\n")


if __name__ == "__main__":
    main()
