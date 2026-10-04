from typing import Optional

from pydantic import BaseModel, Field


class SettingsOut(BaseModel):
    confidence_threshold: float
    iou_threshold: float
    inference_interval_ms: int
    frame_skip: int
    event_cooldown_seconds: int
    alerts_enabled: bool
    sound_enabled: bool
    alert_on_human: bool
    alert_on_animal: bool
    alert_on_vehicle: bool
    snapshot_on_event: bool
    max_snapshot_age_days: int
    model_path: Optional[str] = None

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
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
