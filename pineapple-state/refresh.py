#!/usr/bin/env python3
"""
Legacy Pineapple State Refresh
Deprecated. Canonical runtime state is /Users/DdyFngr/Desktop/Projects/pineapple-ops-runtime/state.
This script now mirrors into canonical state for compatibility and should not be treated as an authoritative legacy writer.
Run standalone: python3 refresh.py
Can also be triggered by OpenClaw or cron at 8am.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LEGACY_STATE_DIR = Path(__file__).parent
CANONICAL_RUNTIME_ROOT = Path(os.environ.get("PINEAPPLE_CONTROL_PLANE_DIR", "/Users/DdyFngr/Desktop/Projects/pineapple-ops-runtime"))
STATE_DIR = CANONICAL_RUNTIME_ROOT / "state"
API_BASE  = os.environ.get("PINEAPPLE_API", "http://localhost:8000")
SCRAPER_ROOT = LEGACY_STATE_DIR.parent
STORAGE_DIR  = SCRAPER_ROOT / "data" / "storage"
DRAFTS_INDEX = SCRAPER_ROOT / "operator-data" / "drafts" / "drafts_index.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch(path: str, fallback=None):
    try:
        with urllib.request.urlopen(f"{API_BASE}{path}", timeout=4) as r:
            return json.loads(r.read())
    except Exception:
        return fallback


def _write(name: str, data) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / name).write_text(json.dumps(data, indent=2, default=str))


def _read(name: str, fallback=None):
    p = STATE_DIR / name
    if not p.exists():
        return fallback if fallback is not None else {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return fallback if fallback is not None else {}


# ---------------------------------------------------------------------------
# system.json
# ---------------------------------------------------------------------------
def build_system() -> dict:
    services = {}

    # Check docker containers
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            timeout=5, stderr=subprocess.DEVNULL
        ).decode()
        for line in out.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                name, status = parts
                services[name] = "up" if "healthy" in status.lower() or "up" in status.lower() else "degraded"
    except Exception:
        pass

    # API liveness
    api_alive = _fetch("/api/health") is not None
    services["marketplace_api"] = "up" if api_alive else "down"

    # OpenClaw liveness (port 18789)
    openclaw_alive = False
    try:
        import socket
        s = socket.socket()
        s.settimeout(1)
        s.connect(("127.0.0.1", 18789))
        s.close()
        openclaw_alive = True
    except Exception:
        pass
    services["openclaw"] = "up" if openclaw_alive else "down"

    # Last scrape from scrape_log
    last_scrape = None
    scrape_log = STORAGE_DIR / "scrape_log.json"
    if scrape_log.exists():
        try:
            runs = json.loads(scrape_log.read_text())
            if runs:
                last_scrape = runs[-1].get("ts")
        except Exception:
            pass

    prior = _read("system.json")
    return {
        "generated_at": _now(),
        "services": services,
        "openclaw_alive": openclaw_alive,
        "api_alive": api_alive,
        "marketplace_last_run": last_scrape,
        "last_openclaw_check": prior.get("last_openclaw_check"),
        "version": "1.0.0",
    }


# ---------------------------------------------------------------------------
# opportunities.json
# ---------------------------------------------------------------------------
def build_opportunities() -> list:
    data = _fetch("/operator/console", {})
    deals = data.get("top_deals", []) if isinstance(data, dict) else []
    return deals[:10]


# ---------------------------------------------------------------------------
# runs.json
# ---------------------------------------------------------------------------
def build_runs() -> list:
    scrape_log = STORAGE_DIR / "scrape_log.json"
    if not scrape_log.exists():
        return []
    try:
        raw = json.loads(scrape_log.read_text())
        runs = []
        for r in raw[-50:]:
            runs.append({
                "source":        r.get("source", "unknown"),
                "started_at":    r.get("ts", ""),
                "duration_ms":   None,
                "listings_found": r.get("total_found", 0),
                "imported":      r.get("imported", 0),
                "alerts_sent":   0,
                "status":        "error" if r.get("error") else "ok",
                "error":         r.get("error"),
            })
        return list(reversed(runs))[:10]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# failures.json
# ---------------------------------------------------------------------------
def build_failures() -> list:
    scrape_log = STORAGE_DIR / "scrape_log.json"
    if not scrape_log.exists():
        return []
    try:
        raw = json.loads(scrape_log.read_text())
        failures = []
        for r in raw:
            if r.get("error"):
                failures.append({
                    "source":      r.get("source", "unknown"),
                    "error":       r.get("error", ""),
                    "occurred_at": r.get("ts", ""),
                    "resolved":    False,
                })
        return list(reversed(failures))[:10]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# alerts.json
# ---------------------------------------------------------------------------
def build_alerts() -> list:
    events_log = STORAGE_DIR / "events.jsonl"
    if not events_log.exists():
        return []
    try:
        lines = events_log.read_text().strip().splitlines()
        alerts = []
        for line in reversed(lines[-100:]):
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("event_type") in ("alert_sent", "action_triggered", "top_deals_updated",
                                            "analyst_decision", "cfo_decision"):
                    alerts.append({
                        "id":         e.get("id"),
                        "ts":         e.get("ts"),
                        "type":       e.get("event_type"),
                        "source":     e.get("source"),
                        "title":      e.get("title"),
                        "message":    e.get("message"),
                        "severity":   e.get("severity", "info"),
                    })
                    if len(alerts) >= 20:
                        break
            except Exception:
                pass
        return alerts
    except Exception:
        return []


# ---------------------------------------------------------------------------
# tasks.json  (from drafts_index — pending drafts = pending tasks)
# ---------------------------------------------------------------------------
def build_tasks() -> list:
    if not DRAFTS_INDEX.exists():
        return []
    try:
        entries = json.loads(DRAFTS_INDEX.read_text())
        tasks = []
        for e in entries:
            if e.get("status") == "draft":
                tasks.append({
                    "id":         e.get("listing_id"),
                    "title":      f"Contact: {e.get('title', 'unknown')}",
                    "owner":      "contact_drafter",
                    "status":     "pending",
                    "priority":   "high" if (e.get("score") or 0) >= 90 else "medium",
                    "created_at": e.get("created_at"),
                })
        return tasks[:20]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# agents.json
# ---------------------------------------------------------------------------
def build_agents(openclaw_alive: bool) -> list:
    return [
        {
            "name":         "chief_of_staff",
            "role":         "Daily brief generator, approval nudger",
            "owner_system": "openclaw",
            "status":       "active" if openclaw_alive else "offline",
            "last_active":  None,
        },
        {
            "name":         "ops_monitor",
            "role":         "Heartbeat checker, failure alerter",
            "owner_system": "openclaw",
            "status":       "active" if openclaw_alive else "offline",
            "last_active":  None,
        },
        {
            "name":         "opportunity_analyst",
            "role":         "Score top deals, generate action recommendations",
            "owner_system": "openclaw",
            "status":       "active" if openclaw_alive else "offline",
            "last_active":  None,
        },
        {
            "name":         "marketplace_scraper",
            "role":         "Multi-source deal ingestion (CL/GovPlanet/PublicSurplus/eBay)",
            "owner_system": "dealscope_api",
            "status":       "active",
            "last_active":  None,
        },
        {
            "name":         "contact_drafter",
            "role":         "Auto-generate buyer contact scripts for score>=80 deals",
            "owner_system": "dealscope_api",
            "status":       "active",
            "last_active":  None,
        },
    ]


# ---------------------------------------------------------------------------
# approvals.json  (bootstrap if missing, else preserve existing)
# ---------------------------------------------------------------------------
def build_approvals() -> list:
    existing = _read("approvals.json", [])
    return existing if existing else []


# ---------------------------------------------------------------------------
# brief.json
# ---------------------------------------------------------------------------
def build_brief(opportunities: list, approvals: list, system: dict) -> dict:
    top3 = []
    for d in opportunities[:3]:
        top3.append({
            "title":            d.get("title"),
            "source":           d.get("source"),
            "price":            d.get("price"),
            "estimated_profit": d.get("effective_profit_after_travel") or d.get("estimated_profit"),
            "action_score":     d.get("action_score"),
            "cos_action":       d.get("cos_action"),
            "listing_url":      d.get("listing_url"),
        })

    pending_count = sum(1 for a in approvals if a.get("status") == "pending")
    services = system.get("services", {})
    up_count  = sum(1 for v in services.values() if v == "up")
    svc_line  = f"{up_count}/{len(services)} services up"
    openclaw  = "online" if system.get("openclaw_alive") else "offline"

    return {
        "generated_at":        _now(),
        "top3":                top3,
        "pending_approvals":   pending_count,
        "system_status":       svc_line,
        "openclaw_status":     openclaw,
        "marketplace_last_run": system.get("marketplace_last_run"),
        "notes":               [],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def refresh_all() -> None:
    t0 = time.time()

    system       = build_system()
    opportunities = build_opportunities()
    runs         = build_runs()
    failures     = build_failures()
    alerts       = build_alerts()
    tasks        = build_tasks()
    agents       = build_agents(system.get("openclaw_alive", False))
    approvals    = build_approvals()
    brief        = build_brief(opportunities, approvals, system)

    _write("system.json",       system)
    _write("opportunities.json", opportunities)
    _write("runs.json",         runs)
    _write("failures.json",     failures)
    _write("alerts.json",       alerts)
    _write("tasks.json",        tasks)
    _write("agents.json",       agents)
    _write("approvals.json",    approvals)
    _write("brief.json",        brief)

    elapsed = time.time() - t0
    print(f"[refresh] done in {elapsed:.2f}s — {len(opportunities)} opps, "
          f"{len(runs)} runs, {len(tasks)} tasks, {len(failures)} failures")


if __name__ == "__main__":
    refresh_all()
