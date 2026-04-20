#!/usr/bin/env python3
"""
Pineapple Brief Generator
Reads canonical pineapple-ops-runtime/state files, computes compressed daily brief, writes brief.json,
and prints Telegram-ready text to stdout.

Can be triggered by OpenClaw, cron (8am), or run standalone:
  python3 generate_brief.py

Cron example:
  0 8 * * * /usr/bin/python3 /path/to/operator-cell/generate_brief.py >> /tmp/brief.log 2>&1
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CANONICAL_RUNTIME_ROOT = Path(os.environ.get("PINEAPPLE_CONTROL_PLANE_DIR", "/Users/DdyFngr/Desktop/Projects/pineapple-ops-runtime"))
STATE_DIR   = CANONICAL_RUNTIME_ROOT / "state"
API_BASE    = os.environ.get("PINEAPPLE_API", "http://localhost:8000")


def _read(name: str, fallback=None):
    p = STATE_DIR / name
    if not p.exists():
        return fallback if fallback is not None else {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return fallback if fallback is not None else {}


def _write(name: str, data) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / name).write_text(json.dumps(data, indent=2, default=str))


def _fetch(path: str, fallback=None):
    try:
        with urllib.request.urlopen(f"{API_BASE}{path}", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return fallback


def build_brief() -> dict:
    # Try live API first; fall back to cached state
    live = _fetch("/operator/console")
    if live and isinstance(live, dict):
        opps = live.get("top_deals", [])
    else:
        opps = _read("opportunities.json", [])
        if isinstance(opps, dict):  # scored_opportunities format
            opps = list(opps.values()) if opps else []

    approvals = _read("approvals.json", [])
    system    = _read("system.json", {})
    runs      = _read("runs.json", [])
    failures  = _read("failures.json", [])

    # Top 3
    top3 = []
    for d in opps[:3]:
        profit = d.get("effective_profit_after_travel") or d.get("estimated_profit") or d.get("estimated_profit_low") or 0
        price  = d.get("price") or 0
        offer  = int(round(price * 0.85 / 5) * 5) if price else 0
        top3.append({
            "title":            d.get("title", "?"),
            "source":           d.get("source", "?"),
            "price":            price,
            "estimated_profit": profit,
            "action_score":     d.get("action_score") or d.get("score") or 0,
            "opening_offer":    offer,
            "cos_action":       d.get("cos_action", ""),
            "listing_url":      d.get("listing_url", ""),
        })

    pending_approvals = sum(1 for a in approvals if a.get("status") == "pending")
    services          = system.get("services", {})
    up_count          = sum(1 for v in services.values() if v == "up")
    openclaw_status   = "online" if system.get("openclaw_alive") else "offline"
    last_run          = system.get("marketplace_last_run", "unknown")
    unresolved_failures = [f for f in failures if not f.get("resolved")]

    # Last pipeline run summary
    last_run_summary = "no runs recorded"
    if runs:
        r = runs[0]
        last_run_summary = (
            f"{r.get('source','?')}: {r.get('imported',0)} imported / "
            f"{r.get('listings_found',0)} found — {r.get('status','?')}"
        )

    brief = {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "top3":                  top3,
        "pending_approvals":     pending_approvals,
        "system_status":         f"{up_count}/{len(services)} services up",
        "openclaw_status":       openclaw_status,
        "marketplace_last_run":  last_run,
        "last_run_summary":      last_run_summary,
        "unresolved_failures":   len(unresolved_failures),
        "notes":                 [],
    }

    _write("brief.json", brief)
    return brief


def format_telegram(brief: dict) -> str:
    top3   = brief.get("top3", [])
    lines  = ["🍍 *PINEAPPLE DAILY BRIEF*", ""]

    if top3:
        medals = ["🥇", "🥈", "🥉"]
        for i, d in enumerate(top3):
            profit = d.get("estimated_profit") or 0
            score  = d.get("action_score") or 0
            lines.append(
                f"{medals[i]} {d['title'][:40]}\n"
                f"   ${d['price']:,.0f} → ${profit:,.0f} profit | score {score:.0f}\n"
                f"   {d.get('cos_action','')}"
            )
        lines.append("")
    else:
        lines.append("No high-score deals right now.")
        lines.append("")

    lines.append(f"📋 Pending approvals: {brief.get('pending_approvals', 0)}")
    lines.append(f"⚙️  System: {brief.get('system_status', '?')}")
    lines.append(f"🤖 OpenClaw: {brief.get('openclaw_status', '?')}")
    lines.append(f"🕐 Last run: {(brief.get('marketplace_last_run') or '?')[:19].replace('T',' ')} UTC")

    failures = brief.get("unresolved_failures", 0)
    if failures:
        lines.append(f"⚠️  Unresolved failures: {failures}")

    return "\n".join(lines)


if __name__ == "__main__":
    brief = build_brief()
    print(format_telegram(brief))
    if "--json" in sys.argv:
        print("\n--- JSON ---")
        print(json.dumps(brief, indent=2))
