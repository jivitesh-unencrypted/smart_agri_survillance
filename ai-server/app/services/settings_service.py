"""Settings service - backed by the single-row Supabase `settings` table."""
from typing import Any, Dict

from app.repositories import settings as settings_repo


def get_settings() -> Dict[str, Any]:
    return settings_repo.get_settings()


def update_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    return settings_repo.update_settings(updates)


def settings_to_out_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """API shape preserved from the original SettingsOut."""
    return {
        "confidence_threshold": row.get("confidence_threshold", 0.25),
        "iou_threshold": row.get("iou_threshold", 0.45),
        "inference_interval_ms": row.get("inference_interval_ms", 400),
        "frame_skip": row.get("frame_skip", 2),
        "event_cooldown_seconds": row.get("event_cooldown_seconds", 8),
        "alerts_enabled": row.get("alerts_enabled", True),
        "sound_enabled": row.get("sound_enabled", True),
        "alert_on_human": row.get("alert_on_human", True),
        "alert_on_animal": row.get("alert_on_animal", True),
        "alert_on_vehicle": row.get("alert_on_vehicle", False),
        "snapshot_on_event": row.get("snapshot_on_event", True),
        "max_snapshot_age_days": row.get("max_snapshot_age_days", 30),
        "sync_enabled": row.get("sync_enabled", True),
        "upload_snapshots_to_cloud": row.get("upload_snapshots_to_cloud", True),
        "stream_max_width": row.get("stream_max_width"),
        "model_path": row.get("model_path"),
        "updated_at": row.get("updated_at"),
    }
