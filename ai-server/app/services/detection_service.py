"""
Detection EVENT grouping.

This is the core of the original app's requirement that we must NOT
create a database row per frame. For each continuously-present object
INSTANCE, a single event row is created and then extended
(end_time / duration / confidence / frame_count bumped) in place. Only
once an instance has not been seen for `event_cooldown_seconds` is its
event considered finished; if it comes back afterwards a brand new event
is created.

Multiple simultaneous instances of the SAME class (e.g. three people in
frame at once) are tracked separately, each with its own event row - they
are matched frame-to-frame by bounding-box IoU against the same class's
other active instances (greedy best-match, no full linear assignment).

Persistence now targets Supabase, and two things were added on top of the
original behaviour:

  1. OFFLINE TOLERANCE - every cloud write is attempted, and on
     SupabaseUnavailable the event (plus any snapshot) is appended to
     the local outbox instead. Detection itself never stops, and the
     `on_new_event` / `on_alert` callbacks still fire so the live UI
     behaves exactly as it always did.

  2. WRITE AMORTISATION - the original extended an event row on every
     processed frame. Each of those is now a network round-trip, so
     extensions are throttled to at most once every
     EVENT_UPDATE_THROTTLE_SECONDS per event, with a final flush when
     the event closes. Live counts, live bounding boxes and the live
     feed are unaffected (they come from memory / the stream); only the
     precision of `duration_seconds` in the history row is coarsened by
     up to a few seconds.

Thread-safety: each camera worker runs in its own thread and only ever
touches its own tracker, so a per-tracker lock is sufficient.
"""
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2

from app import outbox, storage
from app.core.config import settings, SEVERITY_MAP, EVENT_UPDATE_THROTTLE_SECONDS
from app import supabase_client as sc
from app.repositories import alerts as alerts_repo
from app.repositories import detections as detections_repo

logger = logging.getLogger("ai_server.detection")

# Two boxes of the same class across consecutive processed frames are
# considered "the same object" if their IoU is at least this. Unchanged
# from the original app.
IOU_MATCH_THRESHOLD = 0.2

# EVENT_UPDATE_THROTTLE_SECONDS lives in app/core/config.py so it can be
# tuned without touching detection logic.


@dataclass
class _ActiveEvent:
    client_event_id: str
    category: str
    start_time: datetime
    last_seen_mono: float
    max_confidence: float
    conf_sum: float
    frame_count: int
    last_bbox: Tuple[float, float, float, float]
    last_snapshot_mono: float = 0.0
    last_persist_mono: float = 0.0
    db_id: Optional[int] = None
    # The row as it was written when the event opened. `_finalize` replays this
    # (with the closing values merged in) through the outbox when the cloud is
    # unreachable, so it needs the full original column set, not just the delta.
    db_payload: Dict[str, Any] = field(default_factory=dict)


def _iou(box_a, box_b) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
    inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
    inter_w, inter_h = max(0.0, inter_x2 - inter_x1), max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0:
        return 0.0

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    return inter_area / union if union > 0 else 0.0


# ---------------------------------------------------------------------
# Snapshot handling
# ---------------------------------------------------------------------
def save_snapshot(frame_bgr, camera_id: Optional[int], object_name: str) -> Optional[Dict[str, Any]]:
    """
    Writes the annotated frame to local disk and returns the info needed
    to reference it later. Never raises - a failed snapshot must not lose
    the detection event itself.

    Returns {"storage_path", "absolute_path", "file_name"} or None.
    """
    try:
        now_local = datetime.now()
        day = now_local.strftime("%Y-%m-%d")
        target_dir: Path = settings.snapshots_dir / day
        target_dir.mkdir(parents=True, exist_ok=True)

        cam_label = camera_id if camera_id is not None else "video"
        filename = f"cam{cam_label}_{object_name}_{now_local.strftime('%H%M%S_%f')}.jpg"
        target_path = target_dir / filename

        cv2.imwrite(str(target_path), frame_bgr)
        return {
            "storage_path": storage.snapshot_object_path(day, filename),
            "absolute_path": str(target_path),
            "file_name": filename,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save snapshot: %s", exc)
        return None


def _try_upload_snapshot(snapshot: Optional[Dict[str, Any]]) -> None:
    """Best-effort immediate upload; the outbox is the safety net."""
    if not snapshot or not settings.sync_enabled or not settings.upload_snapshots_to_cloud:
        return
    try:
        storage.upload_snapshot_object(Path(snapshot["absolute_path"]), snapshot["storage_path"])
    except sc.SupabaseUnavailable as exc:
        logger.debug("Snapshot upload deferred to outbox: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Snapshot upload failed (will retry via outbox): %s", exc)


# ---------------------------------------------------------------------
# Cloud writes (with offline queueing)
# ---------------------------------------------------------------------
def _write_detection(payload: Dict[str, Any], snapshot: Optional[Dict[str, Any]]):
    """
    Persists a detection event, or queues it when Supabase is
    unreachable. Returns the inserted row, or None when queued.
    """
    if not settings.sync_enabled:
        logger.debug("Cloud sync disabled - keeping detection event local only")
        return None

    try:
        _try_upload_snapshot(snapshot)
        return detections_repo.insert_detection(payload)
    except sc.SupabaseUnavailable as exc:
        logger.warning("Supabase unavailable - queueing detection event locally (%s)", exc)
        outbox.enqueue_detection(
            payload,
            snapshot_file=snapshot["absolute_path"] if snapshot else None,
        )
        return None


def _extend_detection(client_event_id: str, db_id: Optional[int], updates: Dict[str, Any],
                      snapshot: Optional[Dict[str, Any]]) -> None:
    if not settings.sync_enabled or db_id is None:
        return
    try:
        _try_upload_snapshot(snapshot)
        detections_repo.update_detection(db_id, updates)
    except sc.SupabaseUnavailable as exc:
        # Dropping an intermediate duration bump is fine: `_finalize` queues
        # the closing values when the event ends, so the last write that
        # matters is never lost - and queueing every intermediate bump would
        # turn a long event into dozens of redundant rows in the outbox.
        logger.debug("Deferred event extension (Supabase unavailable): %s", exc)


def create_alert_for_detection(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Creates the alert for a newly-opened detection event, honouring the
    user's alert toggles. Returns a broadcast-ready dict, or None when
    alerts are disabled for that category. Mirrors the original
    `alert_service.create_alert_for_detection`.
    """
    from app.services.alert_service import ALERT_TYPE_BY_CATEGORY, TITLES_BY_CATEGORY

    category = event.get("category", "Others")
    try:
        app_settings = sc.call(lambda c: sc.one(c.table("settings").select("*").eq("id", 1).execute())) or {}
    except sc.SupabaseUnavailable:
        # Offline: fall back to "alerts on" so important events are not
        # silently dropped from the queue.
        app_settings = {"alerts_enabled": True, "alert_on_human": True,
                        "alert_on_animal": True, "alert_on_vehicle": False}

    if not app_settings.get("alerts_enabled", True):
        return None
    if category == "Human" and not app_settings.get("alert_on_human", True):
        return None
    if category == "Animals" and not app_settings.get("alert_on_animal", True):
        return None
    if category == "Vehicles" and not app_settings.get("alert_on_vehicle", False):
        return None

    title = TITLES_BY_CATEGORY.get(category, "Object Detected")
    cam_name = event.get("camera_name") or "Camera"
    location = event.get("location") or ""
    obj_name = str(event.get("object_name", "object")).title()

    message = f"{obj_name} detected on {cam_name}"
    if location:
        message += f" ({location})"

    payload = {
        "detection_id": event.get("id"),
        "camera_id": event.get("camera_id"),
        "camera_name": cam_name,
        "category": category,
        "severity": event.get("severity", "normal"),
        "alert_type": ALERT_TYPE_BY_CATEGORY.get(category, "object_detected"),
        "title": title,
        "message": message,
    }

    client_event_id = event.get("client_event_id")
    row = None
    if settings.sync_enabled:
        try:
            row = alerts_repo.insert_alert(payload)
        except sc.SupabaseUnavailable as exc:
            logger.warning("Supabase unavailable - queueing alert locally (%s)", exc)

    if row is None and settings.sync_enabled:
        queued = dict(payload)
        queued["detection_id"] = None
        # Remember which detection this alert belongs to so the outbox can
        # resolve the real id once the event itself has been synced.
        if client_event_id:
            queued["pending_detection_client_event_id"] = client_event_id
        outbox.enqueue_alert(queued)
        return _broadcast_alert_dict(payload, event.get("id"))

    if row is None:
        return None
    return _broadcast_alert_dict(payload, row.get("id"))


def _broadcast_alert_dict(payload: Dict[str, Any], alert_id: Optional[int]) -> Dict[str, Any]:
    return {
        "id": alert_id,
        "detection_id": payload.get("detection_id"),
        "camera_id": payload.get("camera_id"),
        "camera_name": payload.get("camera_name"),
        "category": payload.get("category"),
        "severity": payload.get("severity"),
        "title": payload.get("title"),
        "message": payload.get("message"),
        "acknowledged": False,
        "resolved": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------
# Event tracker
# ---------------------------------------------------------------------
class CameraEventTracker:
    """
    Owns the active-event grouping state for one source stream - either a
    live camera (camera_id set) or a video-analysis job
    (video_analysis_job_id set, camera_id left None).
    """

    def __init__(self, camera_id: Optional[int], camera_name: str, location: str,
                 video_analysis_job_id: Optional[int] = None):
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.location = location
        self.video_analysis_job_id = video_analysis_job_id
        # Keyed by a synthetic per-instance id, e.g. "person#3" - NOT just
        # the object name, so multiple simultaneous instances of the same
        # class each get their own row.
        self._active: Dict[str, _ActiveEvent] = {}
        self._next_instance_seq: Dict[str, int] = {}
        self._lock = threading.Lock()

    def process_detections(
        self,
        detections: list,
        annotated_frame_bgr,
        cooldown_seconds: int,
        snapshot_on_event: bool,
        snapshot_cooldown_seconds: int = 5,
        source: str = "Live Camera",
        on_new_event=None,
        on_alert=None,
    ):
        """Call once per processed (inference) frame."""
        now_mono = time.monotonic()
        now_dt = datetime.now(timezone.utc)

        by_name: Dict[str, List[dict]] = {}
        for det in detections:
            by_name.setdefault(det["name"], []).append(det)

        with self._lock:
            matched_instance_keys = set()

            for name, dets in by_name.items():
                candidate_keys = [k for k, ev in self._active.items() if k.rsplit("#", 1)[0] == name]

                # Greedy matching: highest-confidence detection first, each
                # claiming its best-overlapping still-unclaimed instance.
                dets_sorted = sorted(dets, key=lambda d: d["confidence"], reverse=True)
                unclaimed_keys = set(candidate_keys)

                for det in dets_sorted:
                    bbox = tuple(det["bbox"])
                    category = det["category"]
                    confidence = det["confidence"]

                    best_key, best_iou = None, 0.0
                    for key in unclaimed_keys:
                        iou = _iou(bbox, self._active[key].last_bbox)
                        if iou > best_iou:
                            best_key, best_iou = key, iou

                    if best_key is not None and best_iou >= IOU_MATCH_THRESHOLD:
                        unclaimed_keys.discard(best_key)
                        matched_instance_keys.add(best_key)
                        event = self._active[best_key]
                        event.last_seen_mono = now_mono
                        event.last_bbox = bbox
                        event.frame_count += 1
                        event.conf_sum += confidence
                        event.max_confidence = max(event.max_confidence, confidence)

                        should_snapshot = (
                            snapshot_on_event
                            and annotated_frame_bgr is not None
                            and (now_mono - event.last_snapshot_mono) >= snapshot_cooldown_seconds
                        )
                        should_persist = (now_mono - event.last_persist_mono) >= EVENT_UPDATE_THROTTLE_SECONDS

                        snapshot = None
                        if should_snapshot:
                            snapshot = save_snapshot(annotated_frame_bgr, self.camera_id, name)
                            if snapshot:
                                event.last_snapshot_mono = now_mono

                        event_payload = _event_to_dict(
                            db_id=event.db_id,
                            client_event_id=event.client_event_id,
                            source=source,
                            camera_id=self.camera_id,
                            camera_name=self.camera_name,
                            location=self.location,
                            object_name=name,
                            category=category,
                            severity=SEVERITY_MAP.get(category, "normal"),
                            confidence=event.max_confidence,
                            avg_confidence=event.conf_sum / event.frame_count,
                            frame_count=event.frame_count,
                            start_time=event.start_time,
                            end_time=now_dt,
                            duration_seconds=(now_dt - event.start_time).total_seconds(),
                            snapshot_path=snapshot["storage_path"] if snapshot else None,
                            bounding_box=list(bbox),
                        )

                        if should_persist:
                            updates = {
                                "end_time": now_dt,
                                "duration_seconds": event_payload["duration_seconds"],
                                "confidence": event.max_confidence,
                                "avg_confidence": event_payload["avg_confidence"],
                                "frame_count": event.frame_count,
                                "bounding_box": list(bbox),
                            }
                            if snapshot:
                                updates["snapshot_path"] = snapshot["storage_path"]
                            _extend_detection(event.client_event_id, event.db_id, updates, snapshot)
                            event.last_persist_mono = now_mono

                        if on_new_event:
                            on_new_event(event_payload, is_new=False)
                    else:
                        # No matching active instance - a new, distinct object.
                        seq = self._next_instance_seq.get(name, 0) + 1
                        self._next_instance_seq[name] = seq
                        instance_key = f"{name}#{seq}"
                        matched_instance_keys.add(instance_key)

                        snapshot = None
                        if snapshot_on_event and annotated_frame_bgr is not None:
                            snapshot = save_snapshot(annotated_frame_bgr, self.camera_id, name)

                        client_event_id = str(uuid.uuid4())
                        db_payload = {
                            "source": source,
                            "camera_id": self.camera_id,
                            "camera_name": self.camera_name,
                            "location": self.location,
                            "video_analysis_job_id": self.video_analysis_job_id,
                            "object_name": name,
                            "category": category,
                            "severity": SEVERITY_MAP.get(category, "normal"),
                            "confidence": confidence,
                            "avg_confidence": confidence,
                            "frame_count": 1,
                            "start_time": now_dt,
                            "end_time": now_dt,
                            "duration_seconds": 0.0,
                            "snapshot_path": snapshot["storage_path"] if snapshot else None,
                            "bounding_box": list(bbox),
                            "client_event_id": client_event_id,
                        }

                        row = _write_detection(db_payload, snapshot)
                        db_id = row.get("id") if row else None

                        event_payload = _event_to_dict(
                            db_id=db_id,
                            client_event_id=client_event_id,
                            source=source,
                            camera_id=self.camera_id,
                            camera_name=self.camera_name,
                            location=self.location,
                            object_name=name,
                            category=category,
                            severity=SEVERITY_MAP.get(category, "normal"),
                            confidence=confidence,
                            avg_confidence=confidence,
                            frame_count=1,
                            start_time=now_dt,
                            end_time=now_dt,
                            duration_seconds=0.0,
                            snapshot_path=snapshot["storage_path"] if snapshot else None,
                            bounding_box=list(bbox),
                        )

                        self._active[instance_key] = _ActiveEvent(
                            client_event_id=client_event_id,
                            db_id=db_id,
                            category=category,
                            start_time=now_dt,
                            last_seen_mono=now_mono,
                            max_confidence=confidence,
                            conf_sum=confidence,
                            frame_count=1,
                            last_bbox=bbox,
                            last_snapshot_mono=now_mono,
                            last_persist_mono=now_mono,
                            db_payload=dict(db_payload),
                        )

                        if on_new_event:
                            on_new_event(event_payload, is_new=True)
                        alert_payload = create_alert_for_detection(event_payload)
                        if alert_payload and on_alert:
                            on_alert(alert_payload)

            # Close out instances not seen in this frame beyond the cooldown.
            stale = [
                key for key, ev in self._active.items()
                if key not in matched_instance_keys and (now_mono - ev.last_seen_mono) >= cooldown_seconds
            ]
            for key in stale:
                self._finalize(self._active[key])
                del self._active[key]

    def _finalize(self, event: _ActiveEvent) -> None:
        """
        Final, un-throttled write for an event that has ended.

        If Supabase is unreachable the closing values are queued instead of
        dropped. Dropping them looked safe because a later extension would
        normally carry the same numbers along, but an event that *ends*
        during an outage never extends again, so its row would keep the
        frame_count/duration/confidence from the moment it opened - the
        statistics you actually read in the Detections page would be wrong
        forever, with nothing in the log at warning level to explain why.

        The replay is an upsert on `client_event_id`, which is unique in
        Postgres, so draining the queue updates the existing row instead of
        duplicating the event - and creates it if the opening write never
        arrived either.
        """
        if not settings.sync_enabled:
            return
        now_dt = datetime.now(timezone.utc)
        final_updates = {
            "end_time": now_dt,
            "duration_seconds": (now_dt - event.start_time).total_seconds(),
            "confidence": event.max_confidence,
            "avg_confidence": event.conf_sum / event.frame_count,
            "frame_count": event.frame_count,
            "bounding_box": list(event.last_bbox),
        }
        if event.db_id is None:
            # The opening write never reached the cloud either, so the queued
            # insert is still carrying frame-1 values. Queue a complete row
            # under the same client_event_id; the replay collapses both into
            # one correct row.
            if not event.db_payload:
                return
            outbox.enqueue_detection_final({**event.db_payload, **final_updates})
            return

        try:
            detections_repo.update_detection(event.db_id, final_updates)
        except sc.SupabaseUnavailable as exc:
            logger.warning(
                "Supabase unavailable - queueing final state of event %s (%s)",
                event.client_event_id, exc,
            )
            outbox.enqueue_detection_final({**event.db_payload, **final_updates})

    def close_all(self):
        """Called when a camera stops - finalizes any still-open events."""
        with self._lock:
            for event in list(self._active.values()):
                self._finalize(event)
            self._active.clear()
            self._next_instance_seq.clear()

    def current_counts(self) -> Dict[str, int]:
        counts = {"Human": 0, "Animals": 0, "Vehicles": 0, "Others": 0}
        with self._lock:
            for ev in self._active.values():
                counts[ev.category] = counts.get(ev.category, 0) + 1
        return counts


def _event_to_dict(
    db_id: Optional[int],
    client_event_id: str,
    source: str,
    camera_id: Optional[int],
    camera_name: str,
    location: str,
    object_name: str,
    category: str,
    severity: str,
    confidence: float,
    avg_confidence: float,
    frame_count: int,
    start_time: datetime,
    end_time: datetime,
    duration_seconds: float,
    snapshot_path: Optional[str],
    bounding_box: Optional[List[float]],
) -> Dict[str, Any]:
    """
    Wire format pushed over the WebSocket and used by the live UI.

    `id` is `null` while an event is still buffered in the offline outbox;
    the frontend only uses it as a list key, so nothing breaks, and the
    row appears with its real id as soon as connectivity returns.
    """
    return {
        "id": db_id,
        "client_event_id": client_event_id,
        "source": source,
        "camera_id": camera_id,
        "camera_name": camera_name,
        "location": location,
        "object_name": object_name,
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "avg_confidence": avg_confidence,
        "frame_count": frame_count,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration_seconds,
        "snapshot_path": snapshot_path,
        "bounding_box": bounding_box,
    }
