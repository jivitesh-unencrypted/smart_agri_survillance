"""
Camera repository.

Camera passwords live in `camera_secrets`, a table with no RLS policies,
so this module must always be given the service-role client - which is
exactly what `supabase_client` hands out. Nothing in here is ever
reachable from the browser's anon key.
"""
from typing import Any, Dict, List, Optional

from app import supabase_client as sc
from app.repositories._common import (
    jsonable_payload,
    select_many,
    select_one,
    update_row,
    utc_now_iso,
)


def list_cameras(user_id: str, enabled_only: bool = False) -> List[Dict[str, Any]]:
    """
    Cameras owned by `user_id` only.

    The owner filter is applied HERE as well as by RLS, and that is
    deliberate: this module runs with the service-role key, which BYPASSES
    row level security entirely. RLS protects the browser; it does nothing
    for a service-role process. Without this filter every authenticated
    caller would be handed every user's cameras.
    """
    return select_many("cameras", order_column="id", descending=False,
                       user_id=user_id,
                       enabled=True if enabled_only else None)


def get_camera(camera_id: int, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Loads one camera.

    Pass `user_id` to enforce ownership. Omitting it is only correct for
    internal callers that already know the id is theirs (the stream worker,
    which loaded the row itself).
    """
    return select_one("cameras", id=camera_id, user_id=user_id)


def generate_camera_code(user_id: str) -> str:
    """
    Same CAM-001 / CAM-002 ... scheme as before, but allocated PER USER so
    two operators can each own a "CAM-001" without colliding.
    """
    existing = {
        row["camera_code"]
        for row in sc.call(
            lambda c: sc.rows(
                c.table("cameras").select("camera_code").eq("user_id", user_id).execute()
            )
        )
    }
    n = 1
    while f"CAM-{n:03d}" in existing:
        n += 1
    return f"CAM-{n:03d}"


def create_camera(payload: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    client = sc.get_client()
    if client is None:
        raise sc.SupabaseUnavailable(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in ai-server/.env"
        )
    # The owner is taken from the authenticated token, never from the request
    # body, so a browser cannot create a camera inside somebody else's account.
    payload = {**payload, "user_id": user_id}
    row = sc.one(client.table("cameras").insert(jsonable_payload(payload)).execute())
    if row is None:
        raise sc.SupabaseUnavailable("Insert into cameras returned no row")
    return row


def update_camera(camera_id: int, updates: Dict[str, Any],
                  user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Updates a camera. `user_id` in `updates` is stripped: ownership is set at
    creation from the verified token and is not reassignable through the API.
    """
    updates = {k: v for k, v in updates.items() if k != "user_id"}
    extra = {"user_id": user_id} if user_id is not None else None
    return update_row("cameras", camera_id, updates, extra_filters=extra)


def delete_camera(camera_id: int, user_id: Optional[str] = None) -> bool:
    """
    Removes the camera and its stored password.

    `camera_secrets.camera_id` is `on delete cascade` in the schema, but the
    secret is also deleted explicitly here: a leftover password row keyed on a
    reused id would be silently inherited by the next camera to get that id,
    and camera credentials are the one thing this design refuses to leave
    lying around in the cloud.

    With `user_id` supplied the delete is conditional on ownership, so a
    caller cannot remove somebody else's camera by guessing its id; it
    returns False and the API turns that into a 404.
    """
    def _run(client):
        query = client.table("cameras").delete().eq("id", camera_id)
        if user_id is not None:
            query = query.eq("user_id", user_id)
        deleted = sc.rows(query.execute())
        if not deleted:
            return False
        client.table("camera_secrets").delete().eq("camera_id", camera_id).execute()
        return True

    return sc.call(_run)


def set_runtime_status(
    camera_id: int,
    status: str,
    fps: Optional[float] = None,
    latency_ms: Optional[float] = None,
    message: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """
    Updates only the health columns - called on every telemetry push, so it
    is deliberately a narrow write instead of a whole-row update. The worker
    throttles the call rate to roughly once per second per camera.

    `user_id` is the camera's owner, cached by the worker so this stays a
    single write. It is written onto new status-history rows; the camera row
    itself already has the correct owner and is not re-owned here.
    """
    updates: Dict[str, Any] = {"status": status}
    if fps is not None:
        updates["last_fps"] = fps
    if latency_ms is not None:
        updates["last_latency_ms"] = latency_ms
    if status == "online":
        updates["last_seen_at"] = utc_now_iso()
    updates["last_error"] = message if status == "offline" else None
    update_camera(camera_id, updates)


def record_status_history(camera_id: int, status: str, fps: Optional[float], message: Optional[str],
                          user_id: Optional[str] = None) -> None:
    payload = {
        "camera_id": camera_id,
        "status": status,
        "fps": fps,
        "message": message[:500] if message else None,
    }
    if user_id:
        payload["user_id"] = user_id

    def _run(client):
        client.table("camera_status_history").insert(payload).execute()

    sc.call(_run)


# ---------------------------------------------------------------------
# Secrets (service-role only)
# ---------------------------------------------------------------------
def get_camera_secret(camera_id: int) -> Optional[Dict[str, Any]]:
    return select_one("camera_secrets", camera_id=camera_id)


def set_camera_secret(camera_id: int, password: Optional[str]) -> None:
    """Upserts (or clears, when password is falsy) a camera password."""

    def _run(client):
        if password:
            client.table("camera_secrets").upsert(
                {"camera_id": camera_id, "password": password, "updated_at": utc_now_iso()},
                on_conflict="camera_id",
            ).execute()
        else:
            client.table("camera_secrets").delete().eq("camera_id", camera_id).execute()

    sc.call(_run)
