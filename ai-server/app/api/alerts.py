"""Alert routes - unchanged behaviour, Supabase-backed queries."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import require_authenticated_user
from app.services import alert_service


class AlertOut(BaseModel):
    id: Optional[int] = None
    detection_id: Optional[int] = None
    camera_id: Optional[int] = None
    camera_name: Optional[str] = None
    category: Optional[str] = None
    severity: str
    alert_type: Optional[str] = None
    title: str
    message: str
    acknowledged: bool
    resolved: bool
    status: Optional[str] = None
    created_at: str
    resolved_at: Optional[str] = None


class AlertListResponse(BaseModel):
    items: list[AlertOut]
    total: int


class AlertUpdate(BaseModel):
    acknowledged: Optional[bool] = None
    resolved: Optional[bool] = None


router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse)
def list_alerts(
    user: dict = Depends(require_authenticated_user),
    resolved: Optional[bool] = None,
    severity: Optional[str] = None,
    camera_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
):
    rows = alert_service.list_alerts(resolved=resolved, severity=severity, camera_id=camera_id, limit=limit)
    items = [AlertOut(**alert_service.alert_to_out_dict(r)) for r in rows]
    return AlertListResponse(items=items, total=len(items))


@router.patch("/{alert_id}", response_model=AlertOut)
def update_alert(alert_id: int, payload: AlertUpdate, user: dict = Depends(require_authenticated_user)):
    row = alert_service.update_alert(alert_id, acknowledged=payload.acknowledged, resolved=payload.resolved)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertOut(**alert_service.alert_to_out_dict(row))
