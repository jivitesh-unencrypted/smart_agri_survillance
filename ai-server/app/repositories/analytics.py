"""
Analytics / summary repository.

Aggregation runs inside Postgres via the RPC functions from
`supabase/migrations/0003_analytics_rpc.sql`, re-scoped per owner in
`0005_per_user_isolation.sql`, which return exactly the JSON shapes the
original `analytics_service.py` and `GET /api/detections/summary` produced.
This keeps a growing detection history off the wire and costs no cloud
compute beyond the query itself.

`user_id` is a REQUIRED argument on every function here. The RPCs are
`security invoker`, which scopes them for a browser via RLS - but this
repository connects with the SERVICE-ROLE key, and the service role bypasses
RLS entirely. Calling these without an owner would therefore return the
whole farm's aggregates to any signed-in user, so the owner has to be passed
in explicitly rather than left to default to `auth.uid()` (which is null for
a service-role connection).
"""
from typing import Any, Dict, Optional

from app import supabase_client as sc

ZERO_SUMMARY = {
    "total_events": 0,
    "human_events": 0,
    "animal_events": 0,
    "vehicle_events": 0,
    "other_events": 0,
}


def detection_summary(user_id: str) -> Dict[str, Any]:
    data = sc.call(lambda c: c.rpc("get_detection_summary", {"p_user_id": user_id}).execute())
    raw = getattr(data, "data", None)
    return raw if isinstance(raw, dict) else dict(ZERO_SUMMARY)


def get_analytics(user_id: str) -> Dict[str, Any]:
    data = sc.call(lambda c: c.rpc("get_analytics", {"p_user_id": user_id}).execute())
    raw = getattr(data, "data", None)
    if not isinstance(raw, dict):
        # A failed RPC must not blank out the Analytics page.
        return {"daily": [], "weekly": [], "monthly": [], "by_category": {},
                "by_camera": [], "peak_hours": []}
    return raw


def system_health(user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    `user_id=None` is allowed ONLY for the AI server's own /api/health probe,
    which reports "is the cloud reachable at all", not farm data. Anything
    that renders user-facing numbers must pass the owner.
    """
    data = sc.call(lambda c: c.rpc("get_system_health", {"p_user_id": user_id}).execute())
    raw = getattr(data, "data", None)
    return raw if isinstance(raw, dict) else {"database_ok": False, "total_cameras": 0, "active_cameras": 0}
