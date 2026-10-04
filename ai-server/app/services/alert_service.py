"""
Alert generation and the alert-facing API service.

Severity/title/alert_type rules are preserved exactly from the original
project's utils/alerts.py:

    Human    -> critical / Intrusion Alert
    Animals  -> warning  / Animal Activity
    Vehicles -> info     / Vehicle Activity
    Others   -> normal   / Object Detected

The creation path itself lives in `detection_service.create_alert_for_detection`
so it can be called from the detection worker and can fall back to the
offline outbox in one place. This module owns the query/update side used
by the /api/alerts routes.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import supabase_client as sc
from app.repositories import alerts as alerts_repo
from app.repositories._common import utc_now_iso

TITLES_BY_CATEGORY = {
    "Human": "Intrusion Alert",
    "Animals": "Animal Activity",
    "Vehicles": "Vehicle Activity",
    "Others": "Object Detected",
}

ALERT_TYPE_BY_CATEGORY = {
    "Human": "intrusion",
    "Animals": "animal_activity",
    "Vehicles": "vehicle_activity",
    "Others": "object_detected",
}


def list_alerts(
    resolved: Optional[bool] = None,
    severity: Optional[str] = None,
    camera_id: Optional[int] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    return alerts_repo.list_alerts(resolved=resolved, severity=severity, camera_id=camera_id, limit=limit)


def get_alert(alert_id: int) -> Optional[Dict[str, Any]]:
    return alerts_repo.get_alert(alert_id)


def update_alert(alert_id: int, acknowledged: Optional[bool] = None, resolved: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """
    Applies an acknowledge/resolve patch.

    The `status` column is a STORED generated column in Postgres, so it
    stays consistent with these two booleans with no extra write.
    """
    updates: Dict[str, Any] = {}
    if acknowledged is not None:
        updates["acknowledged"] = acknowledged
    if resolved is not None:
        updates["resolved"] = resolved
        updates["resolved_at"] = utc_now_iso() if resolved else None
    if not updates:
        return alerts_repo.get_alert(alert_id)
    return alerts_repo.update_alert(alert_id, updates)


def alert_to_out_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """API-safe representation (mirrors the original AlertOut shape)."""
    return {
        "id": row.get("id"),
        "detection_id": row.get("detection_id"),
        "camera_id": row.get("camera_id"),
        "camera_name": row.get("camera_name"),
        "category": row.get("category"),
        "severity": row.get("severity"),
        "alert_type": row.get("alert_type"),
        "title": row.get("title"),
        "message": row.get("message"),
        "acknowledged": bool(row.get("acknowledged")),
        "resolved": bool(row.get("resolved")),
        "status": row.get("status"),
        "created_at": _iso(row.get("created_at")),
        "resolved_at": _iso(row.get("resolved_at")),
    }


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)
