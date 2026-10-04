"""
One CameraStreamWorker per running camera.

Runs in its own daemon thread so many cameras stream concurrently without
blocking the FastAPI event loop. This is the efficient pipeline the
project is built around:

    frame -> optional downscale -> frame skip / inference interval
          -> YOLO (only when due) -> event grouping (detection_service)
          -> WebSocket broadcast

Detection does NOT run on every frame - only every `inference_interval_ms`
AND only every Nth frame per `frame_skip` - so a slow CPU stays
responsive and the UI stream itself is never blocked by inference.

Frame capture, YOLO inference, bounding boxes, confidence and
alert/event generation all run here on the local machine. Nothing about
this loop depends on the internet: if Supabase is unreachable the event
is written to the local outbox and detection continues.
"""
import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np

from app import supabase_client as sc
from app.camera.capture import CameraCapture
from app.core.config import CAMERA_RECONNECT_ATTEMPTS, SETTINGS_REFRESH_SECONDS, settings
from app.detection.registry import get_detector
from app.repositories import cameras as cameras_repo
from app.repositories import settings as settings_repo
from app.services import camera_service
from app.services.detection_service import CameraEventTracker
from app.websocket.manager import broadcast_sync

logger = logging.getLogger("ai_server.camera")

_SETTING_KEYS = (
    "confidence_threshold",
    "iou_threshold",
    "inference_interval_ms",
    "frame_skip",
    "event_cooldown_seconds",
    "snapshot_on_event",
    "stream_max_width",
)


def _placeholder_frame(text: str, subtitle: str = "") -> np.ndarray:
    img = np.full((480, 640, 3), (25, 30, 36), dtype=np.uint8)
    cv2.rectangle(img, (0, 0), (640, 6), (0, 140, 255), -1)
    cv2.putText(img, text, (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    if subtitle:
        cv2.putText(img, subtitle, (40, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 180, 190), 1, cv2.LINE_AA)
    return img


def _downscale(frame: np.ndarray, max_width: Optional[int]) -> np.ndarray:
    """Optional resolution reduction - the single biggest CPU saving."""
    if not max_width or max_width <= 0:
        return frame
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    scale = max_width / float(w)
    return cv2.resize(frame, (max_width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


class CameraStreamWorker:
    def __init__(self, camera_id: int, loop: asyncio.AbstractEventLoop):
        self.camera_id = camera_id
        self._loop = loop
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._frame_lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None

        self._actual_fps = 0.0
        self._last_frame_mono: Optional[float] = None

        self._tracker: Optional[CameraEventTracker] = None

    # ---------------------------------------------------------- lifecycle
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._tracker:
            self._tracker.close_all()
        camera_service.set_runtime_status(self.camera_id, "offline", message="Stopped")
        broadcast_sync(self._loop, {
            "type": "camera_status",
            "camera_id": self.camera_id,
            "status": "offline",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._latest_jpeg

    # ---------------------------------------------------------- config
    def _load_camera(self):
        """Reads the camera row once at (re)start."""
        camera = cameras_repo.get_camera(self.camera_id)
        if camera is None:
            return None
        source = camera_service.build_source(camera)
        self._tracker = CameraEventTracker(
            camera["id"], camera.get("name") or "", camera.get("location") or ""
        )
        return source

    def _read_settings(self) -> dict:
        """
        Settings are cached for a few seconds per camera. Re-reading them
        from Supabase on every single frame would be one network call per
        frame; this keeps thresholds live while collapsing that to one
        call per camera per SETTINGS_REFRESH_SECONDS.
        """
        now = time.monotonic()
        if self._settings_cache and (now - self._settings_cached_at) < SETTINGS_REFRESH_SECONDS:
            return self._settings_cache

        row = settings_repo.get_settings_or_defaults()
        # `.get(key, default)` (not `.get(key)`) on purpose: a plain `.get`
        # yields None for an absent column, and `bool(None)` is False, which
        # would silently disable snapshots instead of falling back to the
        # documented default.
        cfg = {
            key: row.get(key, settings_repo.DEFAULT_SETTINGS.get(key))
            for key in _SETTING_KEYS
        }
        self._settings_cache = cfg
        self._settings_cached_at = now
        return cfg

    _settings_cache: Optional[dict] = None
    _settings_cached_at: float = 0.0

    # ---------------------------------------------------------- main loop
    def _run(self):
        try:
            source = self._load_camera()
        except sc.SupabaseUnavailable as exc:
            logger.error("Camera %s could not be loaded from Supabase: %s", self.camera_id, exc)
            return
        if source is None:
            logger.warning("Camera %s not found; worker exiting", self.camera_id)
            return

        capture = CameraCapture(source=source)
        connected = capture.connect()
        camera_service.set_runtime_status(
            self.camera_id, "online" if connected else "offline",
            message=None if connected else "Unable to open camera stream",
        )
        broadcast_sync(self._loop, {
            "type": "camera_status",
            "camera_id": self.camera_id,
            "status": "online" if connected else "offline",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        reconnect_attempts = 0
        frame_counter = 0
        last_inference_mono = 0.0
        last_status_push_mono = 0.0

        try:
            while not self._stop_event.is_set():
                cfg = self._read_settings()
                ret, frame_bgr = capture.read_frame()

                if not ret or frame_bgr is None:
                    reconnect_attempts += 1
                    if reconnect_attempts <= CAMERA_RECONNECT_ATTEMPTS:
                        placeholder = _placeholder_frame(
                            "RECONNECTING...",
                            f"Attempt {reconnect_attempts} of {CAMERA_RECONNECT_ATTEMPTS}",
                        )
                        self._publish_frame(placeholder)
                        capture.reconnect()
                        time.sleep(1.0)
                        continue
                    placeholder = _placeholder_frame("CAMERA OFFLINE", "Check stream URL / connectivity")
                    self._publish_frame(placeholder)
                    camera_service.set_runtime_status(self.camera_id, "offline",
                                                      message="Camera offline - no frames received")
                    broadcast_sync(self._loop, {
                        "type": "camera_status",
                        "camera_id": self.camera_id,
                        "status": "offline",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    time.sleep(2.0)
                    continue

                reconnect_attempts = 0
                frame_counter += 1

                # --- FPS tracking (EMA, as before) ---
                now_mono = time.monotonic()
                if self._last_frame_mono is not None:
                    delta = now_mono - self._last_frame_mono
                    if delta > 0:
                        inst_fps = 1.0 / delta
                        self._actual_fps = inst_fps if self._actual_fps == 0 else (self._actual_fps * 0.8 + inst_fps * 0.2)
                self._last_frame_mono = now_mono

                # --- Optional resolution reduction before inference ---
                inference_frame = _downscale(frame_bgr, cfg.get("stream_max_width"))

                # --- Decide whether this frame gets run through YOLO ---
                frame_skip = max(cfg.get("frame_skip") or 0, 0)
                interval_s = max(cfg.get("inference_interval_ms") or 0, 0) / 1000.0
                due_by_interval = (now_mono - last_inference_mono) >= interval_s
                due_by_skip = (frame_skip == 0) or (frame_counter % (frame_skip + 1) == 0)

                detector = get_detector()
                annotated = inference_frame
                inference_ms = 0.0

                if detector is not None and due_by_interval and due_by_skip:
                    last_inference_mono = now_mono
                    annotated, detections, counts, inference_ms = detector.detect_frame(
                        inference_frame,
                        conf_threshold=cfg.get("confidence_threshold"),
                        iou_threshold=cfg.get("iou_threshold"),
                    )

                    def _on_new_event(payload, is_new):
                        broadcast_sync(self._loop, {
                            "type": "detection",
                            "camera_id": self.camera_id,
                            "is_new_event": is_new,
                            **payload,
                        })

                    def _on_alert(payload):
                        broadcast_sync(self._loop, {"type": "alert", **payload})

                    self._tracker.process_detections(
                        detections,
                        annotated,
                        cooldown_seconds=cfg.get("event_cooldown_seconds") or 8,
                        snapshot_on_event=bool(cfg.get("snapshot_on_event", True)),
                        source="Live Camera",
                        on_new_event=_on_new_event,
                        on_alert=_on_alert,
                    )

                    live_counts = self._tracker.current_counts()
                    broadcast_sync(self._loop, {
                        "type": "detection_count",
                        "camera_id": self.camera_id,
                        "counts": live_counts,
                        "total": sum(live_counts.values()),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

                self._publish_frame(annotated)

                # --- Throttled telemetry / status push (~1/sec) ---
                if (now_mono - last_status_push_mono) >= 1.0:
                    last_status_push_mono = now_mono
                    camera_service.set_runtime_status(self.camera_id, "online",
                                                      fps=self._actual_fps,
                                                      latency_ms=inference_ms or None)
                    broadcast_sync(self._loop, {
                        "type": "fps",
                        "camera_id": self.camera_id,
                        "fps": round(self._actual_fps, 1),
                        "inference_ms": round(inference_ms, 1),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
        finally:
            capture.release()

    def _publish_frame(self, frame_bgr: np.ndarray):
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            with self._frame_lock:
                self._latest_jpeg = buf.tobytes()
