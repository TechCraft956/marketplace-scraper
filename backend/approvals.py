"""
Canonical Approval Manager
Approval-required actions: contact sending, money movement, destructive ops.
State persists to pineapple-ops-runtime/state/approvals.json.
"""
import json
import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from task_audit import append_audit_event, append_task_record, append_task_state_change

logger = logging.getLogger(__name__)

CANONICAL_RUNTIME_ROOT = Path(
    os.environ.get(
        "PINEAPPLE_CONTROL_PLANE_DIR",
        "/Users/DdyFngr/Desktop/Projects/pineapple-ops-runtime",
    )
)
APPROVALS_FILE = CANONICAL_RUNTIME_ROOT / "state" / "approvals.json"
LEGACY_APPROVALS_FILE = Path(os.environ.get("PINEAPPLE_STATE_PATH", "/app/pineapple-state")) / "approvals.json"

APPROVAL_TTL_HOURS = 48

# action_type values
ACTION_SEND_CONTACT  = "send_contact"
ACTION_MARK_PURCHASED = "mark_purchased"
ACTION_DELETE_LISTING = "delete_listing"
ACTION_OTHER          = "other"

# status values
STATUS_PENDING   = "pending"
STATUS_APPROVED  = "approved"
STATUS_REJECTED  = "rejected"
STATUS_EXPIRED   = "expired"
STATUS_AUTO_SAFE = "auto_safe"


def _load() -> list:
    if not APPROVALS_FILE.exists():
        return []
    try:
        return json.loads(APPROVALS_FILE.read_text())
    except Exception:
        return []


def _save(approvals: list) -> None:
    try:
        APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        APPROVALS_FILE.write_text(json.dumps(approvals, indent=2, default=str))
    except Exception as exc:
        logger.warning("approvals: save failed: %s", exc)


def create_approval(action_type: str, title: str, payload: dict, owner: str) -> dict:
    """Create and persist a new approval request in canonical runtime state."""
    now = datetime.now(timezone.utc)
    entry = {
        "id": str(uuid.uuid4()),
        "action_type": action_type,
        "title": title,
        "payload": payload,
        "owner": owner,
        "status": STATUS_PENDING,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=APPROVAL_TTL_HOURS)).isoformat(),
        "resolved_at": None,
        "notes": "",
    }
    approvals = _load()
    approvals.append(entry)
    _save(approvals)

    task_id = f"approval-{entry['id']}"
    append_task_record({
        "task_id": task_id,
        "title": f"Approval required: {title}",
        "owner_agency": owner,
        "assigned_agent": "APEX",
        "source": "approval_manager",
        "priority_band": "p1",
        "priority_score": 90,
        "state": "pending",
        "decision_required": True,
        "expected_output": "Approved or rejected approval record",
        "linked_entities": [payload.get("listing_id"), action_type],
    })
    append_audit_event(
        event_type="approval_created",
        agency=owner,
        agent="APEX",
        summary=title,
        task_id=task_id,
        rationale="Approval-required action entered canonical runtime inbox",
        approval_class="operator_required",
        decision_required=True,
        approval_id=entry["id"],
    )
    return entry


def get_approvals(status: str = None) -> list:
    """Return approvals, optionally filtered by status."""
    expire_stale_approvals()
    approvals = _load()
    if status:
        return [a for a in approvals if a.get("status") == status]
    return approvals


def resolve_approval(approval_id: str, action: str, notes: str = None) -> dict:
    """
    Resolve an approval by id.
    action: "approve" | "reject"
    Returns the updated entry, or raises KeyError if not found.
    """
    approvals = _load()
    for entry in approvals:
        if entry["id"] == approval_id:
            entry["status"]      = STATUS_APPROVED if action == "approve" else STATUS_REJECTED
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            if notes:
                entry["notes"] = notes
            _save(approvals)
            task_id = f"approval-{approval_id}"
            append_task_state_change(
                task_id=task_id,
                state=STATUS_APPROVED if action == "approve" else STATUS_REJECTED,
                agency=entry.get("owner", "approval_manager"),
                agent="APEX",
                summary=f"Approval {action}d: {entry.get('title', approval_id)}",
                approval_id=approval_id,
            )
            append_audit_event(
                event_type="approval_resolved",
                agency=entry.get("owner", "approval_manager"),
                agent="APEX",
                summary=f"Approval {action}d: {entry.get('title', approval_id)}",
                task_id=task_id,
                rationale=notes or "Operator decision recorded",
                approval_class="operator_required",
                decision_required=False,
                approval_id=approval_id,
                action=action,
            )
            return entry
    raise KeyError(f"approval {approval_id} not found")


def expire_stale_approvals() -> int:
    """Mark pending approvals past their expires_at as expired. Returns count expired."""
    approvals = _load()
    now = datetime.now(timezone.utc)
    count = 0
    for entry in approvals:
        if entry.get("status") != STATUS_PENDING:
            continue
        expires_at = entry.get("expires_at")
        if not expires_at:
            continue
        try:
            exp = datetime.fromisoformat(expires_at)
            if now > exp:
                entry["status"]      = STATUS_EXPIRED
                entry["resolved_at"] = now.isoformat()
                count += 1
        except Exception:
            pass
    if count:
        _save(approvals)
    return count
