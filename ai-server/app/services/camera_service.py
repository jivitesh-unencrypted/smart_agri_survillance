"""
Camera service.

Owns camera CRUD and, critically, credential handling. Passwords are
never returned to the client and never stored in the client-readable
`cameras` table - they live in `camera_secrets`, which has no RLS
policies and is only reachable with the service-role key held by this
local process.
"""
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from app import supabase_client as sc
from app.camera.credentials import (
    ENV_PREFIX,
    credential_is_set,
    obfuscate_secret,
    resolve_credential,
)
from app.core.config import CONNECTION_TYPE_LOCAL, NETWORK_CONNECTION_TYPES
from app.repositories import cameras as cameras_repo

logger = logging.getLogger("ai_server.cameras")


def list_cameras(enabled_only: bool = False) -> List[Dict[str, Any]]:
    return cameras_repo.list_cameras(enabled_only=enabled_only)


def get_camera(camera_id: int) -> Optional[Dict[str, Any]]:
    return cameras_repo.get_camera(camera_id)


def delete_camera(camera_id: int) -> bool:
    return cameras_repo.delete_camera(camera_id)


def _resolve_password_field(data: Dict[str, Any], existing: Optional[str]) -> Optional[str]:
    """
    Applies the original `env:` / `obf:` credential scheme:
      * password_is_env -> store a reference to an env var name
      * plain password  -> obfuscate locally
      * blank + keep    -> leave the existing value untouched
    """
    password = data.get("password")
    password_is_env = data.get("password_is_env", False)
    keep_existing = data.get("keep_existing_password", True)

    if keep_existing and not password:
        return existing
    if password_is_env and password:
        return f"{ENV_PREFIX}{password}"
    if password:
        return obfuscate_secret(password)
    return None


def create_camera(data: Dict[str, Any]) -> Dict[str, Any]:
    connection_type = data.get("connection_type", CONNECTION_TYPE_LOCAL)
    password_field = _resolve_password_field(data, None)

    payload = {
        "camera_code": cameras_repo.generate_camera_code(),
        "name": (data.get("name") or "").strip(),
        "location": (data.get("location") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "zone": (data.get("zone") or "").strip(),
        "camera_type": data.get("camera_type", "Local Webcam"),
        "connection_type": connection_type,
        "device_index": data.get("device_index") if connection_type == CONNECTION_TYPE_LOCAL else None,
        "stream_url": _clean_stream_url(data.get("stream_url") or "") if connection_type in NETWORK_CONNECTION_TYPES else "",
        "username": (data.get("username") or "").strip(),
        "enabled": data.get("enabled", True),
        "status": "unknown",
    }

    camera = cameras_repo.create_camera(payload)
    if password_field:
        cameras_repo.set_camera_secret(camera["id"], password_field)
    return camera


def update_camera(camera_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    camera = cameras_repo.get_camera(camera_id)
    if camera is None:
        return None

    existing_secret = cameras_repo.get_camera_secret(camera_id)
    password_field = _resolve_password_field(data, (existing_secret or {}).get("password"))

    connection_type = data.get("connection_type", camera.get("connection_type"))
    updates = {
        "name": (data.get("name") or "").strip(),
        "location": (data.get("location") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "zone": (data.get("zone") or "").strip(),
        "camera_type": data.get("camera_type", camera.get("camera_type")),
        "connection_type": connection_type,
        "device_index": data.get("device_index") if connection_type == CONNECTION_TYPE_LOCAL else None,
        "stream_url": _clean_stream_url(data.get("stream_url") or "") if connection_type in NETWORK_CONNECTION_TYPES else "",
        "username": (data.get("username") or "").strip(),
        "enabled": data.get("enabled", camera.get("enabled", True)),
    }

    updated = cameras_repo.update_camera(camera_id, updates)
    cameras_repo.set_camera_secret(camera_id, password_field)
    return updated


def _clean_stream_url(url: str) -> str:
    """
    Strips any embedded `user:password@` from a stream URL.

    Users often paste a fully-credentialed URL. We refuse to persist those
    credentials in the client-readable `cameras.stream_url` column - the
    password belongs in `camera_secrets`, and the URL is kept clean so it
    is safe for the frontend to display.
    """
    url = (url or "").strip()
    if not url or "@" not in url or "://" not in url:
        return url
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    if not parsed.password and not parsed.username:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    logger.warning(
        "Stripped embedded credentials from a stream URL. Use the username/password "
        "fields (or an env: reference) instead so they are stored safely."
    )
    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, parsed.query, parsed.fragment))


def build_source(camera: Dict[str, Any]):
    """Resolves a camera row to a source usable by CameraCapture."""
    if camera.get("connection_type") == CONNECTION_TYPE_LOCAL:
        return int(camera.get("device_index") or 0)

    url = camera.get("stream_url") or ""
    username = camera.get("username") or ""
    secret = cameras_repo.get_camera_secret(camera["id"])
    password = resolve_credential((secret or {}).get("password") or "")

    if username and "://" in url and "@" not in url:
        scheme, rest = url.split("://", 1)
        if password:
            url = f"{scheme}://{username}:{password}@{rest}"
        else:
            url = f"{scheme}://{username}@{rest}"
    return url


def camera_to_out_dict(camera: Dict[str, Any]) -> Dict[str, Any]:
    """
    API-safe representation - identical shape to the original CameraOut,
    including the guarantee that the password never leaves the server.
    `has_password` is derived from the secret table, not from `cameras`.
    """
    secret = None
    try:
        secret = cameras_repo.get_camera_secret(camera["id"])
    except sc.SupabaseUnavailable:
        # Degrade gracefully: report "no password" rather than 500-ing the
        # whole camera list because the cloud is briefly unreachable.
        logger.warning("Could not read camera secret for camera %s", camera.get("id"))

    if camera.get("connection_type") == CONNECTION_TYPE_LOCAL:
        stream_display = f"Device index {camera.get('device_index') if camera.get('device_index') is not None else 0}"
    else:
        stream_display = camera.get("stream_url") or ""

    return {
        "id": camera.get("id"),
        "camera_code": camera.get("camera_code"),
        "name": camera.get("name"),
        "location": camera.get("location"),
        "description": camera.get("description"),
        "zone": camera.get("zone"),
        "camera_type": camera.get("camera_type"),
        "connection_type": camera.get("connection_type"),
        "device_index": camera.get("device_index"),
        "stream_url_display": stream_display,
        "username": camera.get("username"),
        "has_password": credential_is_set((secret or {}).get("password") or ""),
        "enabled": camera.get("enabled"),
        "status": camera.get("status"),
        "last_fps": camera.get("last_fps"),
        "last_latency_ms": camera.get("last_latency_ms"),
        "last_seen_at": camera.get("last_seen_at"),
        "last_error": camera.get("last_error"),
        "created_at": camera.get("created_at"),
        "updated_at": camera.get("updated_at"),
    }


def set_runtime_status(camera_id: int, status: str, fps: Optional[float] = None,
                       latency_ms: Optional[float] = None, message: Optional[str] = None) -> None:
    """Best-effort health write; never allowed to break the camera loop."""
    try:
        cameras_repo.set_runtime_status(camera_id, status, fps=fps, latency_ms=latency_ms, message=message)
    except sc.SupabaseUnavailable as exc:
        logger.debug("Camera %s status not synced (%s)", camera_id, exc)
