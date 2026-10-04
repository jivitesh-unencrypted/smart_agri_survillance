from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


class AlertOut(BaseModel):
    id: int
    detection_id: Optional[int] = None
    camera_id: Optional[int] = None
    camera_name: Optional[str] = None
    category: Optional[str] = None
    severity: str
    title: str
    message: str
    acknowledged: bool
    resolved: bool
    created_at: datetime
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AlertListResponse(BaseModel):
    items: List[AlertOut]
    total: int


class AlertUpdate(BaseModel):
    acknowledged: Optional[bool] = None
    resolved: Optional[bool] = None
