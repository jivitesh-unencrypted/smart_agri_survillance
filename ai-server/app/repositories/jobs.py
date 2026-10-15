"""Video analysis job repository."""
import uuid
from typing import Any, Dict, List, Optional

from app import supabase_client as sc
from app.repositories._common import select_one, select_many, update_row


def new_job_id() -> str:
    return str(uuid.uuid4())


def insert_job(payload: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    client = sc.get_client()
    # Owner comes from the verified token, never from the multipart form, so a
    # browser cannot upload a video into somebody else's account and have its
    # detections land in their history.
    row = sc.one(client.table("video_analysis_jobs").insert({**payload, "user_id": user_id}).execute())
    if row is None:
        raise sc.SupabaseUnavailable("Insert into video_analysis_jobs returned no row")
    return row


def get_job(job_id: Any, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return select_one("video_analysis_jobs", id=job_id, user_id=user_id)


def list_jobs(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    # Owner filter is mandatory here: this repository uses the service-role
    # key, which bypasses RLS.
    return select_many("video_analysis_jobs", order_column="created_at", limit=limit, user_id=user_id)


def count_jobs(user_id: str) -> int:
    def _run(client):
        result = (
            client.table("video_analysis_jobs")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return result.count or 0

    return sc.call(_run)


def update_job(job_id: Any, updates: Dict[str, Any],
               user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """`user_id` in `updates` is stripped - a job's owner is not reassignable."""
    updates = {k: v for k, v in updates.items() if k != "user_id"}
    extra = {"user_id": user_id} if user_id is not None else None
    return update_row("video_analysis_jobs", job_id, updates, extra_filters=extra)
