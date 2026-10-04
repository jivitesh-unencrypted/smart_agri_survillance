"""Video analysis job repository."""
import uuid
from typing import Any, Dict, List, Optional

from app import supabase_client as sc
from app.repositories._common import select_one, select_many, update_row


def new_job_id() -> str:
    return str(uuid.uuid4())


def insert_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    client = sc.get_client()
    row = sc.one(client.table("video_analysis_jobs").insert(payload).execute())
    if row is None:
        raise sc.SupabaseUnavailable("Insert into video_analysis_jobs returned no row")
    return row


def get_job(job_id: Any) -> Optional[Dict[str, Any]]:
    return select_one("video_analysis_jobs", id=job_id)


def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    return select_many("video_analysis_jobs", order_column="created_at", limit=limit)


def count_jobs() -> int:
    def _run(client):
        result = client.table("video_analysis_jobs").select("id", count="exact").limit(1).execute()
        return result.count or 0

    return sc.call(_run)


def update_job(job_id: Any, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return update_row("video_analysis_jobs", job_id, updates)
