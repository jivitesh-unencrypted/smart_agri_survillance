"""Detection repository - one row per detection EVENT, exactly as before."""
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app import supabase_client as sc
from app.repositories._common import idempotent_insert, update_row
from app.supabase_client import upsert_returning

SEARCH_COLUMNS = ("object_name", "camera_name", "location")


def new_event_id() -> str:
    """Client-generated idempotency key for offline-safe inserts."""
    return str(uuid.uuid4())


def insert_detection(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inserts a detection event. `client_event_id` makes the write
    idempotent, which is what allows the offline outbox to replay a
    buffered event safely after the connection is restored.
    """
    payload.setdefault("client_event_id", new_event_id())
    row = idempotent_insert("detections", payload, "client_event_id")
    if row is None:
        raise sc.SupabaseUnavailable("Insert into detections returned no row")
    return row


def upsert_detection(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inserts a detection event, or UPDATES the existing one that has the same
    `client_event_id`.

    This is deliberately not `insert_detection`. That one is insert-or-return
    -existing, so replaying it with newer values hands back the stale row
    unchanged. The closing values of an event that ended while the cloud was
    unreachable need a real `INSERT ... ON CONFLICT (client_event_id) DO
    UPDATE`, which also covers the case where the row has not reached the
    cloud at all and the upsert is what creates it.
    """
    payload.setdefault("client_event_id", new_event_id())
    row = upsert_returning(sc.get_client(), "detections", payload, "client_event_id")
    if row is None:
        raise sc.SupabaseUnavailable("Upsert into detections returned no row")
    return row


def get_detection_by_client_event_id(client_event_id: str) -> Optional[Dict[str, Any]]:
    """Used by the outbox to resolve a queued alert's detection reference."""
    if not client_event_id:
        return None
    return sc.call(
        lambda c: sc.one(
            c.table("detections").select("*").eq("client_event_id", client_event_id).limit(1).execute()
        )
    )


def update_detection(detection_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return update_row("detections", detection_id, updates)


def get_detection(detection_id: int) -> Optional[Dict[str, Any]]:
    return sc.call(lambda c: sc.one(c.table("detections").select("*").eq("id", detection_id).limit(1).execute()))


def list_detections(
    source: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    camera_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
) -> Tuple[List[Dict[str, Any]], int]:
    """Mirrors GET /api/detections including its filters and pagination."""

    def _filters(query):
        if source:
            query = query.eq("source", source)
        if category:
            query = query.eq("category", category)
        if severity:
            query = query.eq("severity", severity)
        if camera_id:
            query = query.eq("camera_id", camera_id)
        if date_from:
            query = query.gte("created_at", date_from)
        if date_to:
            query = query.lte("created_at", date_to)
        if search:
            # The original matched a case-insensitive substring on
            # object_name only; keep that behaviour identical.
            query = query.ilike("object_name", f"%{search.lower()}%")
        return query

    def _run(client):
        # `count="exact"` makes PostgREST return a Content-Range total, which
        # supabase-py surfaces as `.count` on the *executed response* (there is
        # no `.count` on the query builder). Asking for it on the same query
        # that fetches the page keeps this to a single round trip.
        response = (
            _filters(client.table("detections").select("*", count="exact"))
            .order("created_at", desc=True)
            .range((page - 1) * page_size, page * page_size - 1)
            .execute()
        )
        rows = list(response.data or [])
        total = response.count or len(rows)
        return rows, total

    rows, total = sc.call(_run)
    return rows, total
