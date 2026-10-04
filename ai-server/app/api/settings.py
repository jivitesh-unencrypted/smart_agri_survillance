"""Settings routes - single-row app settings in Supabase."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional

from app.api.deps import require_authenticated_user
from app.services import settings_service


class SettingsUpdate(BaseModel):
    """Validation bounds preserved from the original SettingsUpdate."""
    confidence_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    iou_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    inference_interval_ms: Optional[int] = Field(default=None, ge=0)
    frame_skip: Optional[int] = Field(default=None, ge=0)
    event_cooldown_seconds: Optional[int] = Field(default=None, ge=1)
    alerts_enabled: Optional[bool] = None
    sound_enabled: Optional[bool] = None
    alert_on_human: Optional[bool] = None
    alert_on_animal: Optional[bool] = None
    alert_on_vehicle: Optional[bool] = None
    snapshot_on_event: Optional[bool] = None
    max_snapshot_age_days: Optional[int] = Field(default=None, ge=1)
    sync_enabled: Optional[bool] = None
    upload_snapshots_to_cloud: Optional[bool] = None
    stream_max_width: Optional[int] = Field(default=None, ge=160, le=4096)


router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(user: dict = Depends(require_authenticated_user)):
    return settings_service.settings_to_out_dict(settings_service.get_settings())


@router.put("")
def update_settings(payload: SettingsUpdate, user: dict = Depends(require_authenticated_user)):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    row = settings_service.update_settings(updates)
    return settings_service.settings_to_out_dict(row)
