"""
System routes.

`/health` is the liveness probe and reports the local AI engine, the
cloud connection and the offline queue depth - enough for a human to see
at a glance whether detection is running and whether events are backed
up. It requires no Supabase credentials and never exposes a secret.

`/status` keeps the original SystemStatus shape so the Dashboard is
unchanged, and adds the cloud/sync fields the new architecture can
honestly report.
"""
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app import outbox, supabase_client as sc
from app.api.deps import require_authenticated_user
from app.detection.registry import get_detector, get_load_error
from app.repositories import cameras as cameras_repo

router = APIRouter(prefix="/api/system", tags=["system"])

_START_TIME = time.monotonic()


class SystemStatus(BaseModel):
    """Original fields preserved verbatim; new fields appended."""
    model_loaded: bool
    model_name: str
    model_error: Optional[str] = None
    total_cameras: int
    active_cameras: int
    database_ok: bool
    uptime_seconds: float

    # --- Added by the Supabase migration ---
    cloud_configured: bool = False
    cloud_online: bool = False
    cloud_last_error: Optional[str] = None
    sync_enabled: bool = True
    pending_sync_events: int = 0


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    uptime_seconds: float
    model_loaded: bool
    cloud_configured: bool
    cloud_online: bool
    pending_sync_events: int


def _active_camera_count(request: Request) -> int:
    manager = getattr(request.app.state, "stream_manager", None)
    if manager is None:
        return 0
    return len(manager.active_camera_ids())


@router.get("/status", response_model=SystemStatus)
def system_status(request: Request, user: dict = Depends(require_authenticated_user)):
    detector = get_detector()
    cloud = sc.cloud_status()

    total_cameras = 0
    database_ok = False
    try:
        cameras = cameras_repo.list_cameras()
        total_cameras = len(cameras)
        database_ok = True
    except sc.SupabaseUnavailable:
        database_ok = False

    return SystemStatus(
        model_loaded=detector is not None,
        model_name=getattr(detector, "model_name", None) or ("YOLO" if detector is not None else "unavailable"),
        model_error=get_load_error(),
        total_cameras=total_cameras,
        active_cameras=_active_camera_count(request),
        database_ok=database_ok,
        uptime_seconds=round(time.monotonic() - _START_TIME, 1),
        cloud_configured=cloud["configured"],
        cloud_online=cloud["online"],
        cloud_last_error=cloud["last_error"],
        sync_enabled=getattr(request.app.state, "sync_worker", None) is not None
        and request.app.state.sync_worker.status()["enabled"],
        pending_sync_events=outbox.pending_count(),
    )


@router.get("/health", response_model=HealthResponse)
def health(request: Request):
    """
    Unauthenticated liveness/readiness check for local monitoring.

    Reports `status: "degraded"` rather than failing when Supabase is
    unreachable, because the whole point of the local AI server is that
    detection keeps working through an internet outage.
    """
    detector = get_detector()
    cloud = sc.cloud_status()
    pending = outbox.pending_count()

    if not cloud["configured"]:
        status_str = "degraded"
    elif cloud["online"] and pending == 0:
        status_str = "ok"
    else:
        status_str = "degraded"

    return HealthResponse(
        status=status_str,
        service="smart-agri-ai-server",
        version=request.app.version,
        uptime_seconds=round(time.monotonic() - _START_TIME, 1),
        model_loaded=detector is not None,
        cloud_configured=cloud["configured"],
        cloud_online=cloud["online"],
        pending_sync_events=pending,
    )


@router.get("/config")
def public_config(user: dict = Depends(require_authenticated_user)):
    """
    Non-secret subset of the configuration, so the frontend can show
    effective settings. Deliberately excludes every credential.
    """
    from app.core.config import settings

    return {
        "model_path": str(settings.resolved_model_path),
        "storage_bucket": settings.SUPABASE_STORAGE_BUCKET,
        "sync_enabled": settings.sync_enabled,
        "upload_snapshots": settings.upload_snapshots_to_cloud,
        "default_confidence_threshold": settings.DEFAULT_CONFIDENCE_THRESHOLD,
        "default_iou_threshold": settings.DEFAULT_IOU_THRESHOLD,
        "cloud": sc.cloud_status(),
    }
