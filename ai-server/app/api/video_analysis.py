"""
Video analysis routes.

Uploaded videos are processed on the LOCAL machine with the local YOLO
model - they are never sent to any cloud service. The resulting detection
events and snapshots are what get synced to Supabase, so only small
metadata plus occasional JPEGs ever leave the site.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app import supabase_client as sc
from app.api.deps import require_authenticated_user
from app.core.config import settings
from app.repositories import jobs as jobs_repo
from app.services import video_analysis_service

router = APIRouter(prefix="/api/video-analysis", tags=["video-analysis"])

ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
# Sanity ceiling against an obviously wrong/corrupt upload, not a feature limit.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


class VideoAnalysisJobOut(BaseModel):
    id: Optional[int] = None
    original_filename: str
    status: str
    total_frames: Optional[int] = None
    processed_frames: int = 0
    progress_percent: float = 0.0
    source_fps: Optional[float] = None
    event_count: int = 0
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class VideoAnalysisJobListResponse(BaseModel):
    items: list[VideoAnalysisJobOut]
    total: int


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _row_to_out(row: dict) -> VideoAnalysisJobOut:
    return VideoAnalysisJobOut(
        id=row.get("id"),
        original_filename=row.get("original_filename"),
        status=row.get("status"),
        total_frames=row.get("total_frames"),
        processed_frames=row.get("processed_frames") or 0,
        progress_percent=float(row.get("progress_percent") or 0),
        source_fps=row.get("source_fps"),
        event_count=row.get("event_count") or 0,
        error_message=row.get("error_message"),
        created_at=_iso(row.get("created_at")),
        started_at=_iso(row.get("started_at")),
        completed_at=_iso(row.get("completed_at")),
    )


def _safe_suffix(filename: str) -> str:
    suffix = "".join(ch for ch in (filename.rsplit(".", 1)[-1] if "." in filename else "")
                      if ch.isalnum()).lower()
    return f".{suffix}" if suffix else ""


@router.post("/upload", response_model=VideoAnalysisJobOut, status_code=201)
async def upload_video(file: UploadFile = File(...), user: dict = Depends(require_authenticated_user)):
    original_name = file.filename or "upload.mp4"
    ext = _safe_suffix(original_name)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Unsupported video format '{ext}'. "
                                                      f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    safe_name = re.sub(r"[^\w\-. ]", "_", original_name)[:150]

    try:
        job = jobs_repo.insert_job({
            "original_filename": safe_name,
            "status": "queued",
            "client_job_id": str(uuid.uuid4()),
        })
    except sc.SupabaseUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not create the analysis job: {exc}. Check that Supabase is reachable.",
        ) from exc

    job_id = job.get("id")
    dest_path = settings.uploads_dir / f"job{job_id}_{uuid.uuid4().hex[:8]}{ext}"

    size = 0
    try:
        with open(dest_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Video file is too large.")
                out.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        _fail_job(job_id)
        raise
    except Exception:
        dest_path.unlink(missing_ok=True)
        _fail_job(job_id)
        raise HTTPException(status_code=500, detail="Failed to save uploaded video.")

    video_analysis_service.start_job(job_id, dest_path)
    return _row_to_out(job)


def _fail_job(job_id):
    try:
        jobs_repo.update_job(job_id, {
            "status": "failed",
            "error_message": "Upload failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:  # noqa: BLE001
        pass


@router.get("/jobs", response_model=VideoAnalysisJobListResponse)
def list_jobs(user: dict = Depends(require_authenticated_user)):
    rows = jobs_repo.list_jobs()
    items = [_row_to_out(r) for r in rows]
    return VideoAnalysisJobListResponse(items=items, total=len(items))


@router.get("/jobs/{job_id}", response_model=VideoAnalysisJobOut)
def get_job(job_id: int, user: dict = Depends(require_authenticated_user)):
    row = jobs_repo.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _row_to_out(row)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, user: dict = Depends(require_authenticated_user)):
    if not video_analysis_service.request_cancel(job_id):
        raise HTTPException(status_code=400, detail="Job is not running or does not exist")
    return {"status": "cancel_requested"}
