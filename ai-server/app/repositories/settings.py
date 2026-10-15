"""
Settings repository - PER USER.

The original app used a single global row (`id` is always 1) because every
account shared one farm. With one user == one isolated farm that is wrong: a
global row means changing a threshold as User A silently retunes User B's
cameras, and both users would read and write the same record.

The row is now keyed on `user_id`, so each authenticated user has exactly one
settings row, created with defaults on first access. Which settings apply to
a running camera is decided by the CAMERA'S OWNER, not by whoever happens to
be signed in to the browser, so detection is reproducible.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app import supabase_client as sc
from app.core.config import settings as app_settings
from app.repositories._common import upsert_row, utc_now_iso

logger = logging.getLogger("ai_server.repo.settings")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "confidence_threshold": 0.25,
    "iou_threshold": 0.45,
    "inference_interval_ms": 400,
    "frame_skip": 2,
    "event_cooldown_seconds": 8,
    "alerts_enabled": True,
    "sound_enabled": True,
    "alert_on_human": True,
    "alert_on_animal": True,
    "alert_on_vehicle": False,
    "snapshot_on_event": True,
    "max_snapshot_age_days": 30,
    "sync_enabled": True,
    "upload_snapshots_to_cloud": True,
    "stream_max_width": None,
}

# Last-known-good settings, kept on disk next to the offline outbox.
#
# Without this, a camera that loses its internet link reverts to
# DEFAULT_SETTINGS mid-flight: if the operator had tuned confidence_threshold
# down to catch smaller intrusions, every camera silently went back to 0.25 and
# stopped reporting the thing they cared about - with no error anywhere, since
# "cloud unreachable, use defaults" is a perfectly reasonable thing for a
# repository to do in isolation. It is only wrong because detection is
# supposed to keep running on the configuration the operator actually chose.
#
# The cache is PER USER, named after the owner, because a shared file would
# let User B's thresholds overwrite User A's cached copy and vice versa - the
# exact cross-user bleed this repository now exists to prevent.
_settings_cache_dir: Optional[Path] = None


def _cache_dir() -> Path:
    global _settings_cache_dir
    if _settings_cache_dir is None:
        _settings_cache_dir = app_settings.data_dir / "settings_cache"
    return _settings_cache_dir


def _cache_path(user_id: str) -> Path:
    # user_id is a Supabase Auth uuid, so it is safe as a filename; the
    # `unknown` bucket covers the rare call before an owner is known.
    safe = (user_id or "unknown").replace("/", "_").replace("\\", "_")
    return _cache_dir() / f"{safe}.json"


def _write_cache(user_id: str, row: Dict[str, Any]) -> None:
    """Best-effort; a read-only disk must not break settings reads."""
    try:
        path = _cache_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in row.items() if k in DEFAULT_SETTINGS}
        path.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not cache settings locally: %s", exc)


def _read_cache(user_id: str) -> Dict[str, Any]:
    try:
        raw = _cache_path(user_id).read_text(encoding="utf-8")
        cached = json.loads(raw)
    except Exception:  # noqa: BLE001 - absent or corrupt cache is not an error
        return {}
    if not isinstance(cached, dict):
        return {}
    return {k: v for k, v in cached.items() if k in DEFAULT_SETTINGS}


def _merge(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Defaults first, then any non-NULL value from `row`."""
    if not row:
        return dict(DEFAULT_SETTINGS)
    return dict(DEFAULT_SETTINGS, **{k: v for k, v in row.items() if v is not None})


def get_settings(user_id: str) -> Dict[str, Any]:
    """
    Returns this user's settings row, creating it with defaults if missing.

    When Supabase is unreachable the last-known-good settings are served from
    a local per-user cache written by the previous successful read, so an
    offline camera keeps running on the thresholds its owner actually
    configured. Only if that cache is missing too do we fall back to
    DEFAULT_SETTINGS.

    The stored row is merged *over* DEFAULT_SETTINGS and explicit NULLs are
    dropped. This matters: callers such as the camera worker's `_read_settings`
    build their config with `row.get(key)`, which yields None - not the
    default - for any column the row happens not to carry. A None there makes
    `bool(cfg.get("snapshot_on_event", True))` evaluate to False, silently
    turning snapshots off with no warning anywhere. Merging here keeps every
    key present and correctly typed for all consumers.
    """
    try:
        row = _select_row(user_id)
        if row is not None:
            merged = _merge(row)
            _write_cache(user_id, merged)
            return merged

        seeded = upsert_row("settings", {"user_id": user_id, **DEFAULT_SETTINGS}, "user_id")
        merged = _merge(seeded)
        _write_cache(user_id, merged)
        return merged
    except sc.SupabaseUnavailable:
        cached = _read_cache(user_id)
        if cached:
            logger.info(
                "Supabase unreachable; using last-known settings for user %s from %s",
                (user_id or "unknown")[:8], _cache_path(user_id).name,
            )
        return _merge(cached)


def _select_row(user_id: str) -> Optional[Dict[str, Any]]:
    def _run(client):
        return sc.one(
            client.table("settings").select("*").eq("user_id", user_id).limit(1).execute()
        )

    return sc.call(_run)


def get_settings_or_defaults(user_id: Optional[str]) -> Dict[str, Any]:
    """Never raises - used on the hot detection path."""
    try:
        return get_settings(user_id) if user_id else dict(DEFAULT_SETTINGS)
    except sc.SupabaseUnavailable:
        return _merge(_read_cache(user_id or ""))


def update_settings(user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Partial update of THIS user's settings row.

    `user_id` is pinned into the payload so the upsert's conflict target is
    the payload itself. Relying on a column default for the conflict target
    would be exactly the bug that let partial saves insert a second row.
    Ownership is never taken from `updates`.
    """
    payload = {k: v for k, v in updates.items() if v is not None and k != "user_id"}
    if not payload:
        return get_settings(user_id)
    payload["user_id"] = user_id
    payload["updated_at"] = utc_now_iso()
    row = upsert_row("settings", payload, "user_id")
    if row is None:
        raise sc.SupabaseUnavailable("Settings upsert returned no row")
    merged = _merge(row)
    _write_cache(user_id, merged)
    return merged
