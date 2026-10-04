"""
Background video-file analysis.

Runs in a daemon thread so the upload endpoint returns immediately with a
job id and the frontend polls GET /api/video-analysis/jobs/{id} for
progress - unchanged from the original app.

Reuses the exact same detector + event-grouping pipeline as live cameras
(`CameraEventTracker`); the only difference is that frames come from a
finite video file, so `total_frames` is known up front.

Two things changed for the cloud-architecture migration:

  * Uploaded videos are stored in the LOCAL `storage/uploads/` folder and
    deleted after analysis, as before. Nothing large is ever written to
    Postgres or to Supabase Storage - only the resulting detection events
    and their snapshots are synced.
  * Job progress is written to Supabase (service role) rather than SQLite.
    A failed write degrades progress reporting but never stops the
    analysis itself.
"""
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import cv2

from app import supabase_client as sc
from app.core.config import settings
from app.detection.registry import get_detector
from app.repositories import jobs as jobs_repo
from app.repositories import settings as settings_repo
from app.services.detection_service import CameraEventTracker

logger = logging.getLogger("ai_server.video")

# In-process registry so /cancel can signal a running thread.
_JOB_THREADS: Dict[int, threading.Thread] = {}
_JOB_LOCK = threading.Lock()


def start_job(job_id: int, file_path: Path):
    thread = threading.Thread(target=_run_job, args=(job_id, file_path), daemon=True, name=f"video-job-{job_id}")
    with _JOB_LOCK:
        _JOB_THREADS[job_id] = thread
    thread.start()


def request_cancel(job_id: int) -> bool:
    job = jobs_repo.get_job(job_id)
    if job is None or job.get("status") not in ("queued", "processing"):
        return False
    jobs_repo.update_job(job_id, {"cancel_requested": True})
    return True


def _update_job(job_id: int, **fields):
    """Never raises - progress reporting must not abort an analysis run."""
    try:
        jobs_repo.update_job(job_id, fields)
    except sc.SupabaseUnavailable as exc:
        logger.debug("Job %s progress not synced (%s)", job_id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not update job %s: %s", job_id, exc)


def _is_cancel_requested(job_id: int) -> bool:
    try:
        job = jobs_repo.get_job(job_id)
        return bool(job and job.get("cancel_requested"))
    except sc.SupabaseUnavailable:
        # Offline: fail open and keep processing rather than silently
        # dropping a long-running analysis. The cancel will be honoured
        # once connectivity returns.
        return False


def _run_job(job_id: int, file_path: Path):
    from datetime import datetime, timezone

    _update_job(job_id, status="processing", started_at=datetime.now(timezone.utc).isoformat())

    detector = get_detector()
    if detector is None:
        _update_job(job_id, status="failed", error_message="YOLO model is not loaded on the server.",
                    completed_at=datetime.now(timezone.utc).isoformat())
        _cleanup(file_path, job_id)
        return

    cap = cv2.VideoCapture(str(file_path))
    if not cap.isOpened():
        _update_job(job_id, status="failed", error_message="Could not open the uploaded video file.",
                    completed_at=datetime.now(timezone.utc).isoformat())
        _cleanup(file_path, job_id)
        return

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None
    source_fps = cap.get(cv2.CAP_PROP_FPS) or None
    _update_job(job_id, total_frames=total_frames, source_fps=source_fps)

    cfg = settings_repo.get_settings_or_defaults()
    tracker = CameraEventTracker(
        camera_id=None,
        camera_name=file_path.name,
        location="Uploaded Video",
        video_analysis_job_id=job_id,
    )

    frame_index = 0
    last_progress_push = 0.0
    event_count = 0

    def _on_new_event(payload, is_new):
        nonlocal event_count
        if is_new:
            event_count += 1

    try:
        while True:
            if _is_cancel_requested(job_id):
                _update_job(job_id, status="cancelled", completed_at=datetime.now(timezone.utc).isoformat())
                tracker.close_all()
                return

            ret, frame = cap.read()
            if not ret or frame is None:
                break  # end of video

            frame_index += 1
            frame_skip = max(cfg.get("frame_skip") or 0, 0)
            due = (frame_skip == 0) or (frame_index % (frame_skip + 1) == 0)

            if due:
                annotated, detections, _counts, _inference_ms = detector.detect_frame(
                    frame,
                    conf_threshold=cfg.get("confidence_threshold"),
                    iou_threshold=cfg.get("iou_threshold"),
                )
                tracker.process_detections(
                    detections,
                    annotated,
                    cooldown_seconds=cfg.get("event_cooldown_seconds", 8),
                    snapshot_on_event=cfg.get("snapshot_on_event", True),
                    source="Video Analysis",
                    on_new_event=_on_new_event,
                )

            now = time.monotonic()
            if total_frames and (now - last_progress_push) >= 0.5:
                last_progress_push = now
                progress = min(99.0, (frame_index / total_frames) * 100.0)
                _update_job(job_id, processed_frames=frame_index, progress_percent=progress,
                            event_count=event_count)

        tracker.close_all()
        _update_job(
            job_id,
            status="completed",
            processed_frames=frame_index,
            progress_percent=100.0,
            event_count=event_count,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:  # noqa: BLE001 - a failed job must never crash the server
        logger.exception("Video analysis job %s failed", job_id)
        _update_job(job_id, status="failed", error_message=str(e)[:1000],
                    completed_at=datetime.now(timezone.utc).isoformat())
    finally:
        cap.release()
        _cleanup(file_path, job_id)


def _cleanup(file_path: Path, job_id: int):
    """
    Uploaded source videos are temporary - only the logged events and
    snapshots persist. Nothing large is retained in the cloud.
    """
    try:
        if file_path.exists():
            file_path.unlink()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not delete uploaded video %s: %s", file_path, exc)
    with _JOB_LOCK:
        _JOB_THREADS.pop(job_id, None)
