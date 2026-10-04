"""
Analytics service.

All aggregation now happens inside Postgres
(`supabase/migrations/0003_analytics_rpc.sql`), which returns exactly the
structure this function returned when it ran Python queries against
SQLite. This keeps a large detection history off the browser entirely
and costs no continuous cloud compute.
"""
from typing import Any, Dict

from app.repositories import analytics as analytics_repo


def get_analytics() -> Dict[str, Any]:
    return analytics_repo.get_analytics()


def detection_summary() -> Dict[str, Any]:
    return analytics_repo.detection_summary()
