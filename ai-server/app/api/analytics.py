"""Analytics routes - aggregation now runs in Postgres."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import require_authenticated_user
from app.schemas.system import AnalyticsResponse
from app.services import analytics_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("", response_model=AnalyticsResponse)
def get_analytics(user: dict = Depends(require_authenticated_user)):
    # Aggregates are computed over THIS user's detections only. The owner is
    # passed explicitly because the analytics RPC is reached with the
    # service-role key, which bypasses the RLS policies that would otherwise
    # scope it.
    return analytics_service.get_analytics(user["id"])
