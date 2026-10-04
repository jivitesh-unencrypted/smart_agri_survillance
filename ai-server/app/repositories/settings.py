"""
Settings repository.

The original app used a single-row `settings` table (id is always 1) with
app-wide, not per-user, configuration. That is preserved exactly, so the
existing Settings screen keeps working unchanged. The `user_id` column
suggested in some designs is deliberately NOT added here because no
per-user settings existed before and inventing one would create two
sources of truth for the same knobs.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app import supabase_client as sc
from app.core.config import settings as app_settings
from app.repositories._common import select_one, upsert_row, utc_now_iso

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
_settings_cache_path: Optional[Path] = None


def _cache_path() -> Path:
    global _settings_cache_path
    if _settings_cache_path is None:
        _settings_cache_path = app_settings.data_dir / "settings_cache.json"
    return _settings_cache_path


def _write_cache(row: Dict[str, Any]) -> None:
    """Best-effort; a read-only disk must not break settings reads."""
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in row.items() if k in DEFAULT_SETTINGS}
        path.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not cache settings locally: %s", exc)


def _read_cache() -> Dict[str, Any]:
    try:
        raw = _cache_path().read_text(encoding="utf-8")
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


def get_settings() -> Dict[str, Any]:
    """
    Returns the settings row, creating it with defaults if missing.

    When Supabase is unreachable the last-known-good settings are served from
    a local cache written by the previous successful read, so an offline
    camera keeps running on the thresholds the operator actually configured.
    Only if that cache is missing too do we fall back to DEFAULT_SETTINGS.

    The stored row is merged *over* DEFAULT_SETTINGS and explicit NULLs are
    dropped. This matters: callers such as the camera worker's `_read_settings`
    build their config with `row.get(key)`, which yields None - not the
    default - for any column the row happens not to carry. A None there makes
    `bool(cfg.get("snapshot_on_event", True))` evaluate to False, silently
    turning snapshots off with no warning anywhere. Merging here keeps every
    key present and correctly typed for all consumers.
    """
    try:
        row = select_one("settings", id=1)
        if row is not None:
            merged = _merge(row)
            _write_cache(merged)
            return merged

        seeded = upsert_row("settings", {"id": 1, **DEFAULT_SETTINGS}, "id")
        merged = _merge(seeded)
        _write_cache(merged)
        return merged
    except sc.SupabaseUnavailable:
        cached = _read_cache()
        if cached:
            logger.info(
                "Supabase unreachable; using last-known settings from %s", _cache_path().name
            )
        return _merge(cached)


def get_settings_or_defaults() -> Dict[str, Any]:
    """Never raises - used on the hot detection path."""
    try:
        return get_settings()
    except sc.SupabaseUnavailable:
        return _merge(_read_cache())


def update_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Partial update of the single settings row.

    `id` must be pinned to 1 explicitly. Without it the upsert's conflict
    target is never supplied by the payload, so `INSERT ... ON CONFLICT (id)`
    only works because the column happens to default to 1 - and against any
    Postgres where that default is absent, every partial save from the
    Settings screen would silently insert a *second* settings row instead of
    updating the existing one.
    """
    payload = {k: v for k, v in updates.items() if v is not None}
    if not payload:
        return get_settings()
    payload["id"] = 1
    payload["updated_at"] = utc_now_iso()
    row = upsert_row("settings", payload, "id")
    if row is None:
        raise sc.SupabaseUnavailable("Settings upsert returned no row")
    return row
