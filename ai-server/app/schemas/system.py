from typing import Dict, List, Optional

from pydantic import BaseModel


class SystemStatus(BaseModel):
    model_loaded: bool
    model_name: str
    model_error: Optional[str] = None
    total_cameras: int
    active_cameras: int
    database_ok: bool
    uptime_seconds: float


class TimeseriesPoint(BaseModel):
    bucket: str
    Human: int = 0
    Animals: int = 0
    Vehicles: int = 0
    Others: int = 0


class CameraWiseCount(BaseModel):
    camera_name: str
    count: int


class AnalyticsResponse(BaseModel):
    daily: List[TimeseriesPoint]
    weekly: List[TimeseriesPoint]
    monthly: List[TimeseriesPoint]
    by_category: Dict[str, int]
    by_camera: List[CameraWiseCount]
    peak_hours: List[Dict[str, int]]
