"""Authenticated frame-by-frame inference for browser-captured cameras.

The browser owns camera permission and capture (getUserMedia). Only small JPEG
frames are sent here; this server never attempts to open a browser camera.

Two modes share one endpoint:

* Ad-hoc frame (no ``camera_id``): YOLO runs and the boxes are returned. Nothing
  is stored. Kept for backwards compatibility.
* Browser camera (``camera_id`` of a ``browser_webcam`` row): the camera must
  first be started with ``POST /sessions/{id}/start``. Frames then go through the
  SAME ``CameraEventTracker`` the network cameras use, so detections, alerts,
  snapshots and the outbox behave exactly as for an IP camera, and telemetry is
  broadcast to the owner over the WebSocket.
"""
import asyncio
import logging
import threading
import time
from typing import Any, Dict, Optional

import cv2
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_authenticated_user
from app.core.config import BROWSER_CONNECTION_TYPES
from app.detection.registry import get_detector, get_load_error
from app.repositories import settings as settings_repo
from app.services import camera_service
from app.services.detection_service import CameraEventTracker
from app.websocket.manager import broadcast_sync

logger = logging.getLogger("ai_server.live")

router = APIRouter(prefix="/api/live-detection", tags=["live detection"])

_inference_lock = threading.Lock()
_sessions_lock = threading.Lock()
# (owner user id, camera id) -> running browser-camera session.
_sessions: Dict[tuple, "_BrowserSession"] = {}

_MAX_FRAME_BYTES = 1_500_000
_MAX_SIDE = 1280
# YOLO input size for browser frames. 416 is roughly 2.4x fewer pixels than the
# default 640, which matters most on a small hosted CPU.
_BROWSER_IMGSZ = 416
_SETTINGS_TTL_SECONDS = 10.0
_ALLOWED_FRAME_TYPES = {"image/jpeg", "image/jpg", "image/png", "application/octet-stream"}


class _BrowserSession:
    def __init__(self, camera: Dict[str, Any], user_id: str):
        self.camera_id = camera["id"]
        self.user_id = user_id
        self.tracker = CameraEventTracker(
            camera["id"],
            camera.get("name") or "",
            camera.get("location") or "",
            user_id=user_id,
        )
        # Frames of one session are processed one at a time so the tracker's
        # event grouping sees them in order.
        self.lock = threading.Lock()
        self._settings: Optional[Dict[str, Any]] = None
        self._settings_at = 0.0
        self._last_frame_at: Optional[float] = None
        self.fps = 0.0

    def settings(self) -> Dict[str, Any]:
        """Owner's detection settings, cached briefly (one DB read per interval, not per frame)."""
        now = time.monotonic()
        if self._settings is None or now - self._settings_at >= _SETTINGS_TTL_SECONDS:
            self._settings = settings_repo.get_settings_or_defaults(self.user_id)
            self._settings_at = now
        return self._settings

    def note_frame(self) -> None:
        now = time.monotonic()
        if self._last_frame_at is not None and now > self._last_frame_at:
            inst = 1.0 / (now - self._last_frame_at)
            self.fps = inst if self.fps == 0 else self.fps * 0.8 + inst * 0.2
        self._last_frame_at = now


def _announce_status(loop, camera_id: int, user_id: str, status: str) -> None:
    broadcast_sync(loop, {
        "type": "camera_status",
        "camera_id": camera_id,
        "status": status,
    }, user_id=user_id)


_BUSY = object()


def _infer(detector, image: np.ndarray, conf, iou, draw: bool):
    if not _inference_lock.acquire(blocking=False):
        return _BUSY
    started = time.monotonic()
    try:
        result = detector.detect_frame(image, conf_threshold=conf, iou_threshold=iou, draw=draw,
                                       imgsz=_BROWSER_IMGSZ)
        logger.info("YOLO frame done in %.0f ms (image %dx%d)",
                    (time.monotonic() - started) * 1000, image.shape[1], image.shape[0])
        return result
    finally:
        _inference_lock.release()


def _process_session_frame(session: _BrowserSession, annotated, detections, loop) -> Dict[str, int]:
    """Feeds one processed frame into the shared event tracker (runs in a worker thread)."""
    cfg = session.settings()
    user_id = session.user_id
    camera_id = session.camera_id

    def _on_new_event(payload, is_new):
        broadcast_sync(loop, {
            "type": "detection",
            "camera_id": camera_id,
            "is_new_event": is_new,
            **payload,
        }, user_id=user_id)

    def _on_alert(payload):
        broadcast_sync(loop, {"type": "alert", **payload}, user_id=user_id)

    with session.lock:
        session.note_frame()
        session.tracker.process_detections(
            detections,
            annotated,
            cooldown_seconds=cfg.get("event_cooldown_seconds") or 8,
            snapshot_on_event=bool(cfg.get("snapshot_on_event", True)),
            source="Browser Webcam",
            on_new_event=_on_new_event,
            on_alert=_on_alert,
        )
        live_counts = session.tracker.current_counts()

    broadcast_sync(loop, {
        "type": "detection_count",
        "camera_id": camera_id,
        "counts": live_counts,
        "total": sum(live_counts.values()),
    }, user_id=user_id)
    broadcast_sync(loop, {
        "type": "fps",
        "camera_id": camera_id,
        "fps": round(session.fps, 1),
        "inference_ms": 0.0,
    }, user_id=user_id)
    return live_counts


def discard_browser_session(user_id: str, camera_id: int) -> None:
    """Drops a session without an HTTP call, for when its camera row is deleted."""
    with _sessions_lock:
        session = _sessions.pop((user_id, camera_id), None)
    if session is not None:
        session.tracker.close_all()


def _track_frame_safely(session, annotated, detections, loop) -> None:
    """Runs event tracking; a storage problem is logged and never fails the frame."""
    try:
        _process_session_frame(session, annotated, detections, loop)
    except Exception:  # noqa: BLE001
        logger.exception("Browser camera %s: tracking failed", session.camera_id)


def _owned_camera_or_404(camera_id: int, user_id: str) -> Dict[str, Any]:
    camera = camera_service.get_camera(camera_id, user_id=user_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


@router.post("/sessions/{camera_id}/start")
async def start_browser_session(camera_id: int, user: dict = Depends(require_authenticated_user)):
    """Marks a browser_webcam camera as live so its frames are tracked and broadcast."""
    camera = await run_in_threadpool(_owned_camera_or_404, camera_id, user["id"])
    if camera.get("connection_type") not in BROWSER_CONNECTION_TYPES:
        raise HTTPException(status_code=400, detail="Only browser webcams use frame sessions")
    if not camera.get("enabled"):
        raise HTTPException(status_code=400, detail="Camera is disabled")

    key = (user["id"], camera_id)
    with _sessions_lock:
        if key in _sessions:
            return {"status": "running", "camera_id": camera_id}
        _sessions[key] = _BrowserSession(camera, user["id"])

    await run_in_threadpool(
        camera_service.set_runtime_status, camera_id, "online",
        fps=None, latency_ms=None, message=None, user_id=user["id"],
    )
    _announce_status(asyncio.get_running_loop(), camera_id, user["id"], "online")
    return {"status": "running", "camera_id": camera_id}


@router.post("/sessions/{camera_id}/stop")
async def stop_browser_session(camera_id: int, user: dict = Depends(require_authenticated_user)):
    """Ends the session, finalising any open detection events."""
    await run_in_threadpool(_owned_camera_or_404, camera_id, user["id"])

    with _sessions_lock:
        session = _sessions.pop((user["id"], camera_id), None)
    if session is not None:
        await run_in_threadpool(session.tracker.close_all)

    await run_in_threadpool(
        camera_service.set_runtime_status, camera_id, "offline",
        fps=None, latency_ms=None, message="Browser camera stopped", user_id=user["id"],
    )
    _announce_status(asyncio.get_running_loop(), camera_id, user["id"], "offline")
    return {"status": "stopped", "camera_id": camera_id}


@router.post("/frame")
async def detect_browser_frame(
    frame: UploadFile = File(...),
    camera_id: Optional[int] = Form(None),
    background: BackgroundTasks = None,
    user: dict = Depends(require_authenticated_user),
):
    """
    Run YOLO on one browser-captured JPEG and return normalized box data.

    The reply goes out as soon as inference finishes. Event tracking (Supabase
    writes, snapshots) runs in a background task: on a small hosted CPU those
    writes can take longer than the proxy's timeout, which the browser then sees
    as a 502 even though the detection was recorded.
    """
    if frame.content_type not in _ALLOWED_FRAME_TYPES:
        raise HTTPException(status_code=415, detail="Send a JPEG or PNG frame")

    session = None
    if camera_id is not None:
        with _sessions_lock:
            session = _sessions.get((user["id"], int(camera_id)))
        if session is None:
            # The owner is part of the key, so another user's camera id also lands here.
            raise HTTPException(status_code=409, detail="Browser camera is not started. Start it before sending frames.")

    data = await frame.read(_MAX_FRAME_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Frame is empty")
    if len(data) > _MAX_FRAME_BYTES:
        raise HTTPException(status_code=413, detail="Frame is too large; reduce camera resolution")

    detector = get_detector()
    if detector is None:
        reason = get_load_error()
        raise HTTPException(status_code=503, detail=f"YOLO model is not available on this server{': ' + reason if reason else ''}")

    encoded = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Could not decode frame image")

    height, width = image.shape[:2]
    if max(width, height) > _MAX_SIDE:
        scale = _MAX_SIDE / max(width, height)
        image = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        height, width = image.shape[:2]

    conf = iou = None
    if session is not None:
        cfg = await run_in_threadpool(session.settings)
        conf = cfg.get("confidence_threshold")
        iou = cfg.get("iou_threshold")

    try:
        outcome = await run_in_threadpool(
            _infer, detector, image, conf, iou, session is not None,
        )
    except Exception as exc:  # keep model errors from surfacing as internal details
        logger.exception("YOLO inference failed")
        raise HTTPException(status_code=503, detail="YOLO inference failed; check the AI server logs") from exc

    if outcome is _BUSY:
        # Refused, not queued: the browser retries this frame shortly.
        raise HTTPException(
            status_code=429,
            detail="The AI server is busy analysing another frame. Retrying shortly.",
            headers={"Retry-After": "2"},
        )
    annotated, detections, frame_counts, inference_ms = outcome

    counts = frame_counts
    if session is not None:
        loop = asyncio.get_running_loop()
        track = _track_frame_safely
        if background is not None:
            background.add_task(track, session, annotated, detections, loop)
        else:
            track(session, annotated, detections, loop)

    return {
        "width": width,
        "height": height,
        "detections": detections,
        "counts": counts,
        "frame_counts": frame_counts,
        # For browser cameras `counts` are the objects in this frame. Event grouping
        # (the Detections page) is tracked in the background.
        "inference_ms": round(inference_ms, 1),
        "camera_id": camera_id,
        "tracked": session is not None,
    }
