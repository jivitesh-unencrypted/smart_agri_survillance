"""
Analytics / summary repository.

Aggregation runs inside Postgres via the RPC functions created in
`supabase/migrations/0003_analytics_rpc.sql`, which return exactly the
JSON shapes the original `analytics_service.py` and
`GET /api/detections/summary` produced. This keeps a growing detection
history off the wire and costs no cloud compute beyond the query itself.
"""
from typing import Any, Dict

from app import supabase_client as sc

ZERO_SUMMARY = {
    "total_events": 0,
    "human_events": 0,
    "animal_events": 0,
    "vehicle_events": 0,
    "other_events": 0,
}


def detection_summary() -> Dict[str, Any]:
    data = sc.call(lambda c: c.rpc("get_detection_summary").execute())
    raw = getattr(data, "data", None)
    return raw if isinstance(raw, dict) else dict(ZERO_SUMMARY)


def get_analytics() -> Dict[str, Any]:
    data = sc.call(lambda c: c.rpc("get_analytics").execute())
    raw = getattr(data, "data", None)
    if not isinstance(raw, dict):
        # A failed RPC must not blank out the Analytics page.
        return {"daily": [], "weekly": [], "monthly": [], "by_category": {},
                "by_camera": [], "peak_hours": []}
    return raw


def system_health() -> Dict[str, Any]:
    data = sc.call(lambda c: c.rpc("get_system_health").execute())
    raw = getattr(data, "data", None)
    return raw if isinstance(raw, dict) else {"database_ok": False, "total_cameras": 0, "active_cameras": 0}
