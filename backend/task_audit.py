import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(os.environ.get("PINEAPPLE_CONTROL_PLANE_DIR", "/Users/DdyFngr/Desktop/Projects/pineapple-ops-runtime"))
STATE_DIR = BASE_DIR / "state"
LOG_DIR = BASE_DIR / "logs"
TASKS_PATH = STATE_DIR / "tasks.jsonl"
AUDIT_PATH = LOG_DIR / "audit.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    _ensure()
    with path.open("a") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def append_task_record(task: dict[str, Any]) -> dict[str, Any]:
    record = dict(task)
    record.setdefault("task_id", f"task-{uuid.uuid4().hex[:12]}")
    record.setdefault("created_at", _now())
    record.setdefault("updated_at", record["created_at"])
    _append_jsonl(TASKS_PATH, record)
    return record


def append_task_state_change(task_id: str, state: str, agency: str, agent: str, summary: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "task_id": task_id,
        "state": state,
        "owner_agency": agency,
        "assigned_agent": agent,
        "summary": summary,
        "updated_at": _now(),
    }
    payload.update(extra)
    _append_jsonl(TASKS_PATH, payload)
    return payload


def append_audit_event(event_type: str, agency: str, agent: str, summary: str, task_id: Optional[str] = None, rationale: Optional[str] = None, approval_class: str = "autonomous_safe", decision_required: bool = False, **extra: Any) -> dict[str, Any]:
    event = {
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "task_id": task_id,
        "timestamp": _now(),
        "agency": agency,
        "agent": agent,
        "event_type": event_type,
        "summary": summary,
        "rationale": rationale,
        "approval_class": approval_class,
        "decision_required": decision_required,
    }
    event.update(extra)
    _append_jsonl(AUDIT_PATH, event)
    return event
