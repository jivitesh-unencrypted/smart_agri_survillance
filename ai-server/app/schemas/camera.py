from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class CameraBase(BaseModel):
    name: str
    location: Optional[str] = ""
    description: Optional[str] = ""
    zone: Optional[str] = ""
    camera_type: str = "Local Webcam"
    connection_type: str = "local_webcam"  # local_webcam | ip_camera | rtsp | http_mjpeg
    device_index: Optional[int] = 0
    stream_url: Optional[str] = ""
    username: Optional[str] = ""
    enabled: bool = True

    @field_validator("connection_type")
    @classmethod
    def _valid_connection_type(cls, v):
        allowed = {"local_webcam", "ip_camera", "rtsp", "http_mjpeg"}
        if v not in allowed:
            raise ValueError(f"connection_type must be one of {sorted(allowed)}")
        return v


class CameraCreate(CameraBase):
    password: Optional[str] = None
    password_is_env: bool = False


class CameraUpdate(CameraBase):
    password: Optional[str] = None
    password_is_env: bool = False
    keep_existing_password: bool = True


class CameraOut(BaseModel):
    id: int
    camera_code: str
    name: str
    location: Optional[str] = ""
    description: Optional[str] = ""
    zone: Optional[str] = ""
    camera_type: str
    connection_type: str
    device_index: Optional[int] = None
    stream_url_display: Optional[str] = ""   # credentials never returned to the client
    username: Optional[str] = ""
    has_password: bool = False
    enabled: bool
    status: str
    last_fps: Optional[float] = None
    last_latency_ms: Optional[float] = None
    last_seen_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CameraTestResult(BaseModel):
    success: bool
    message: str
    latency_ms: Optional[float] = None
