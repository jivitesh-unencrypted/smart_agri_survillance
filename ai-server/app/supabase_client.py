"""
Supabase access layer for the LOCAL AI server.

Design goals
------------
1. Never crash the local detection pipeline because the internet is down.
   Every call is wrapped so a network/Supabase failure raises a single,
   well-known exception type (`SupabaseUnavailable`) that callers can
   choose to queue instead of propagate.
2. Hold the ONLY copy of the service-role key. This module is imported
   exclusively by server-side code - nothing here is ever bundled into
   the frontend.
3. Be a single shared client (httpx connection pooling matters) built
   lazily, so the process still boots and can run cameras even when
   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured yet.
"""
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger("ai_server.supabase")

_client = None
_client_lock = threading.Lock()

# How long a single successful probe keeps reporting `cloud_online: true`.
# Comfortably longer than the SyncWorker's ping interval (see
# SYNC_INTERVAL_SECONDS) so a healthy link never flickers to "degraded".
ONLINE_STALE_AFTER_SECONDS = 45.0

# Simple connectivity tracking so /api/system/status and the frontend can
# honestly report cloud state instead of guessing.
_state = {
    "configured": settings.supabase_configured,
    "last_ok_mono": None,
    "last_error": None,
    "consecutive_failures": 0,
}
_state_lock = threading.Lock()


class SupabaseUnavailable(RuntimeError):
    """Raised when Supabase is unreachable or not configured.

    Callers treat this as "put it in the outbox and keep detecting".
    """


def is_configured() -> bool:
    return settings.supabase_configured


def get_client():
    """
    Returns the shared service-role client, or None when Supabase has not
    been configured. Never raises for a missing or placeholder
    configuration - the local server must still boot and run cameras.
    """
    global _client

    if not settings.supabase_configured:
        return None

    if _client is None:
        with _client_lock:
            if _client is None:
                try:
                    from supabase import ClientOptions, create_client

                    _client = create_client(
                        settings.SUPABASE_URL,
                        settings.SUPABASE_SERVICE_ROLE_KEY,
                        options=ClientOptions(
                            # The SDK otherwise retries internally, which hides
                            # outages from our own health tracking.
                            httpx_client=_build_httpx_client(),
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    _mark_failed(exc)
                    logger.error(
                        "Could not create the Supabase client (%s). Check SUPABASE_URL and "
                        "SUPABASE_SERVICE_ROLE_KEY in ai-server/.env - detection will keep "
                        "running locally and events will be queued.",
                        exc,
                    )
                    return None
                logger.info("Supabase client initialised for %s", settings.SUPABASE_URL)
    return _client


def _build_httpx_client():
    import httpx

    return httpx.Client(
        timeout=httpx.Timeout(15.0, connect=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        # Retries are handled by the outbox instead, so failures surface fast.
        transport=httpx.HTTPTransport(retries=0),
    )


def _mark_ok():
    with _state_lock:
        _state["last_ok_mono"] = time.monotonic()
        _state["last_error"] = None
        _state["consecutive_failures"] = 0


def _mark_failed(exc: Exception):
    with _state_lock:
        _state["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
        _state["consecutive_failures"] += 1


def cloud_status() -> Dict[str, Any]:
    """
    Honest snapshot of cloud connectivity for the health endpoints.

    `online` deliberately means "reachable as of a RECENT probe", not
    "reachable at some point since boot". Without the staleness window a
    server that lost its internet link an hour ago would keep reporting
    `cloud_online: true` and `/health` would wrongly say `status: ok`.
    """
    with _state_lock:
        last_ok = _state["last_ok_mono"]
        age = (time.monotonic() - last_ok) if last_ok is not None else None
        online = age is not None and age <= ONLINE_STALE_AFTER_SECONDS
        return {
            "configured": bool(_state["configured"]),
            "online": online,
            "last_success_seconds_ago": (round(age, 1) if age is not None else None),
            "consecutive_failures": _state["consecutive_failures"],
            "last_error": _state["last_error"],
        }


def ping() -> bool:
    """Cheap round-trip used by the outbox loop and the health endpoint."""
    client = get_client()
    if client is None:
        return False
    try:
        client.table("settings").select("id").limit(1).execute()
        _mark_ok()
        return True
    except Exception as exc:  # noqa: BLE001
        _mark_failed(exc)
        return False


def call(fn, *args, **kwargs):
    """
    Runs a Supabase operation, normalising every failure into
    SupabaseUnavailable so callers have exactly one thing to catch.
    """
    client = get_client()
    if client is None:
        raise SupabaseUnavailable(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in ai-server/.env"
        )
    try:
        result = fn(client, *args, **kwargs)
        _mark_ok()
        return result
    except SupabaseUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        _mark_failed(exc)
        raise SupabaseUnavailable(str(exc)) from exc


def rows(result) -> List[Dict[str, Any]]:
    """Normalises a PostgREST result into plain dicts."""
    data = getattr(result, "data", None)
    return data if isinstance(data, list) else []


def one(result) -> Optional[Dict[str, Any]]:
    """Normalises a PostgREST result into a single dict (or None)."""
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def insert_returning(client, table: str, payload: Dict[str, Any],
                     upsert: bool = False, on_conflict: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """INSERT ... RETURNING * - the Supabase equivalent of db.add()/db.refresh()."""
    if client is None:
        # Must be the shared, well-known exception: callers decide whether to
        # queue the write instead. An AttributeError here would escape into the
        # camera worker thread and kill detection.
        raise SupabaseUnavailable(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in ai-server/.env"
        )
    query = client.table(table).insert(payload)
    if upsert:
        query = query.upsert(payload, on_conflict=on_conflict or "id")
    return one(query.execute())


def upsert_returning(client, table: str, payload: Dict[str, Any], on_conflict: str) -> Optional[Dict[str, Any]]:
    if client is None:
        raise SupabaseUnavailable(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in ai-server/.env"
        )
    return one(client.table(table).upsert(payload, on_conflict=on_conflict).execute())


# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------
def verify_access_token(access_token: str) -> Optional[Dict[str, Any]]:
    """
    Verifies a Supabase Auth access token presented by the frontend and
    returns the authenticated user, or None when the token is missing,
    expired or invalid.

    This is what keeps sensitive operations (camera CRUD with
    credentials, start/stop, test, video analysis) server-side while
    still requiring the user to be logged in - exactly the guarantee the
    old JWT dependency provided, now backed by Supabase Auth.
    """
    if not access_token:
        return None
    client = get_client()
    if client is None:
        return None
    try:
        response = client.auth.get_user(access_token)
        _mark_ok()
        user = getattr(response, "user", None)
        if user is None:
            return None
        return {
            "id": str(user.id),
            "email": getattr(user, "email", None),
            "username": (getattr(user, "user_metadata", None) or {}).get("username"),
        }
    except Exception as exc:  # noqa: BLE001 - an invalid token is a normal condition
        logger.debug("Access token rejected by Supabase Auth: %s", exc)
        return None


def get_user_profile(user_id: str) -> Optional[Dict[str, Any]]:
    """Loads the application profile row (role, is_active, ...)."""
    return call(lambda c: one(c.table("users").select("*").eq("id", user_id).limit(1).execute()))


def ensure_user_profile(user_id: str, email: Optional[str], username: Optional[str]) -> Dict[str, Any]:
    """
    Creates the profile row on first login if it does not exist yet, so
    an account made through the Supabase dashboard is usable by the app
    without any extra step.
    """
    existing = get_user_profile(user_id)
    if existing is not None:
        return existing

    row = insert_returning(
        get_client(),
        "users",
        {"id": user_id, "email": email, "username": username or email, "role": "user", "is_active": True},
    )
    return row or {"id": user_id, "email": email, "username": username or email, "role": "user", "is_active": True}


def touch_last_seen(user_id: str):
    """Best-effort `last_seen_at` update; never worth failing a request over."""
    try:
        call(
            lambda c: c.table("users")
            .update({"last_seen_at": "now()"})
            .eq("id", user_id)
            .execute(),
        )
    except SupabaseUnavailable:
        pass
