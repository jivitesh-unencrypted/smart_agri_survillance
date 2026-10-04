"""Analytics routes - aggregation now runs in Postgres."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import require_authenticated_user
from app.schemas.system import AnalyticsResponse
from app.services import analytics_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("", response_model=AnalyticsResponse)
def get_analytics(user: dict = Depends(require_authenticated_user)):
    return analytics_service.get_analytics()
