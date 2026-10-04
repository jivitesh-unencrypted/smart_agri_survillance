"""Alert repository."""
import uuid
from typing import Any, Dict, List, Optional

from app import supabase_client as sc
from app.repositories._common import idempotent_insert, select_one, update_row


def new_alert_id() -> str:
    return str(uuid.uuid4())


def insert_alert(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload.setdefault("client_alert_id", new_alert_id())
    row = idempotent_insert("alerts", payload, "client_alert_id")
    if row is None:
        raise sc.SupabaseUnavailable("Insert into alerts returned no row")
    return row


def get_alert(alert_id: int) -> Optional[Dict[str, Any]]:
    return select_one("alerts", id=alert_id)


def list_alerts(
    resolved: Optional[bool] = None,
    severity: Optional[str] = None,
    camera_id: Optional[int] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    def _run(client):
        q = client.table("alerts").select("*")
        if resolved is not None:
            q = q.eq("resolved", resolved)
        if severity:
            q = q.eq("severity", severity)
        if camera_id:
            q = q.eq("camera_id", camera_id)
        return sc.rows(q.order("created_at", desc=True).limit(limit).execute())

    return sc.call(_run)


def update_alert(alert_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return update_row("alerts", alert_id, updates)
