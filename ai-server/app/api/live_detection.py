"""Authenticated frame-by-frame inference for webcams captured in the browser.

The browser owns camera permission and capture. Only small JPEG frames are sent
here; this server never attempts to open the user's webcam device itself.
"""
import threading

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from app.api.deps import require_authenticated_user
from app.detection.registry import get_detector, get_load_error

router = APIRouter(prefix="/api/live-detection", tags=["live detection"])
_inference_lock = threading.Lock()
_MAX_FRAME_BYTES = 1_500_000
_MAX_SIDE = 1280


@router.post("/frame")
async def detect_browser_frame(
    frame: UploadFile = File(...),
    user: dict = Depends(require_authenticated_user),
):
    """Run YOLO on one browser-captured JPEG and return normalized box data."""
    if frame.content_type not in {"image/jpeg", "image/jpg", "image/png", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Send a JPEG or PNG frame")

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

    # YOLO's shared model instance is protected from overlapping frame calls.
    try:
        with _inference_lock:
            _annotated, detections, counts, inference_ms = detector.detect_frame(image, draw=False)
    except Exception as exc:  # keep model errors from surfacing as internal details
        raise HTTPException(status_code=503, detail="YOLO inference failed; check the AI server logs") from exc

    return {
        "width": width,
        "height": height,
        "detections": detections,
        "counts": counts,
        "inference_ms": round(inference_ms, 1),
    }
