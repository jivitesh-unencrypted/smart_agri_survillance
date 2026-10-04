from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


class DetectionOut(BaseModel):
    id: int
    source: str
    camera_id: Optional[int] = None
    camera_name: Optional[str] = None
    location: Optional[str] = None
    object_name: str
    category: str
    severity: str
    confidence: float
    avg_confidence: float
    frame_count: int
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    snapshot_path: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class DetectionListResponse(BaseModel):
    items: List[DetectionOut]
    total: int
    page: int
    page_size: int


class DetectionSummary(BaseModel):
    total_events: int
    human_events: int
    animal_events: int
    vehicle_events: int
    other_events: int
