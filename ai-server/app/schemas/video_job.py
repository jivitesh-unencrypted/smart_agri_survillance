from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class VideoAnalysisJobOut(BaseModel):
    id: int
    original_filename: str
    status: str
    total_frames: Optional[int] = None
    processed_frames: int
    progress_percent: float
    source_fps: Optional[float] = None
    event_count: int
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class VideoAnalysisJobListResponse(BaseModel):
    items: List[VideoAnalysisJobOut]
    total: int
