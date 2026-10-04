"""
Detection routes.

Filtering, pagination and response shape are identical to the original
implementation - the only change is that the queries now run against
Supabase Postgres through PostgREST instead of local SQLite.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import require_authenticated_user
from app.repositories import detections as detections_repo
from app.services import analytics_service

router = APIRouter(prefix="/api/detections", tags=["detections"])


class DetectionOut(BaseModel):
    id: Optional[int] = None
    client_event_id: Optional[str] = None
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
    start_time: str
    end_time: str
    duration_seconds: float
    snapshot_path: Optional[str] = None
    bounding_box: Optional[list] = None
    created_at: str


class DetectionListResponse(BaseModel):
    items: list[DetectionOut]
    total: int
    page: int
    page_size: int


class DetectionSummary(BaseModel):
    total_events: int
    human_events: int
    animal_events: int
    vehicle_events: int
    other_events: int


def _iso(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _row_to_out(row: dict) -> DetectionOut:
    return DetectionOut(
        id=row.get("id"),
        client_event_id=row.get("client_event_id"),
        source=row.get("source"),
        camera_id=row.get("camera_id"),
        camera_name=row.get("camera_name"),
        location=row.get("location"),
        object_name=row.get("object_name"),
        category=row.get("category"),
        severity=row.get("severity"),
        confidence=float(row.get("confidence") or 0),
        avg_confidence=float(row.get("avg_confidence") or 0),
        frame_count=int(row.get("frame_count") or 0),
        start_time=_iso(row.get("start_time")),
        end_time=_iso(row.get("end_time")),
        duration_seconds=float(row.get("duration_seconds") or 0),
        snapshot_path=row.get("snapshot_path"),
        bounding_box=row.get("bounding_box"),
        created_at=_iso(row.get("created_at")),
    )


@router.get("", response_model=DetectionListResponse)
def list_detections(
    user: dict = Depends(require_authenticated_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    source: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    camera_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    search: Optional[str] = None,
):
    items, total = detections_repo.list_detections(
        source=source,
        category=category,
        severity=severity,
        camera_id=camera_id,
        date_from=_iso(date_from) if date_from else None,
        date_to=_iso(date_to) if date_to else None,
        search=search,
        page=page,
        page_size=page_size,
    )
    return DetectionListResponse(
        items=[_row_to_out(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/summary", response_model=DetectionSummary)
def detections_summary(user: dict = Depends(require_authenticated_user)):
    return DetectionSummary(**analytics_service.detection_summary())
