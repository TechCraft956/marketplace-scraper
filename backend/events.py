import json
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from collections import deque
from typing import Optional

EVENTS_LOG = Path("/app/storage/events.jsonl")
_event_buffer: deque = deque(maxlen=500)
_sse_queues: list = []

EVENT_TYPES = [
    "scraper_started", "scraper_finished", "deals_imported",
    "top_deals_updated", "analyst_decision", "cfo_decision", "cos_decision",
    "alert_sent", "action_triggered", "pipeline_run", "error", "stale_job", "system_info"
]

SEVERITY = {"info": "info", "warn": "warn", "error": "error", "critical": "critical"}


def emit(event_type: str, source: str, title: str, message: str,
         severity: str = "info", metadata: dict = None) -> dict:
    event = {
        "id": f"{int(time.time() * 1000)}-{source[:6]}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "source": source,
        "title": title,
        "message": message,
        "severity": severity,
        "metadata": metadata or {},
    }
    _event_buffer.appendleft(event)
    try:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_LOG, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass
    for q in list(_sse_queues):
        try:
            q.put_nowait(event)
        except Exception:
            pass
    return event


def get_recent(limit: int = 100) -> list:
    events = list(_event_buffer)[:limit]
    if len(events) < limit and EVENTS_LOG.exists():
        try:
            lines = EVENTS_LOG.read_text().strip().split("\n")
            disk_events = [json.loads(l) for l in lines[-limit:] if l]
            seen = {e["id"] for e in events}
            for e in reversed(disk_events):
                if e["id"] not in seen:
                    events.append(e)
                    seen.add(e["id"])
            events = sorted(events, key=lambda x: x["ts"], reverse=True)[:limit]
        except Exception:
            pass
    return events


def register_sse_queue(q):
    _sse_queues.append(q)


def unregister_sse_queue(q):
    try:
        _sse_queues.remove(q)
    except ValueError:
        pass
