"""
Smart Agri Surveillance - Modal video worker.
Separate from modal_worker.py. Trigger only from trusted authenticated backend:
    process_video_job.spawn(job_id, authenticated_user_id)

Modal Secret required: smart-agri-supabase
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
import os
import time
import uuid
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import modal
import requests

app = modal.App("smart-agri-video-worker")
VIDEOS_BUCKET = "videos"
SNAPSHOTS_BUCKET = "snapshots"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "ffmpeg")
    .pip_install(
        "supabase>=2.32.0",
        "ultralytics>=8.3.0",
        "opencv-python-headless>=4.9.0",
        "numpy>=1.26.0",
        "requests>=2.31.0",
        "fastapi>=0.115.0",
    )
    .add_local_file(
        str(Path(__file__).parent / "models" / "yolov8n.pt"),
        remote_path="/root/models/yolov8n.pt",
    )
)

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def supabase_client():
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Modal Supabase secret is missing required variables.")
    return create_client(url, key)

def owned_job(client, job_id, user_id):
    rows = (
        client.table("video_analysis_jobs").select("*")
        .eq("id", int(job_id)).eq("user_id", str(user_id)).limit(1)
        .execute().data
    )
    if not rows:
        raise RuntimeError("Job not found for specified owner.")
    return rows[0]

def update_job(client, job_id, user_id, **fields):
    rows = (
        client.table("video_analysis_jobs").update(fields)
        .eq("id", int(job_id)).eq("user_id", str(user_id)).execute().data
    )
    if not rows:
        raise RuntimeError("Job update failed or owner did not match.")
    return rows[0]

def download_video(client, object_path, destination):
    signed = client.storage.from_(VIDEOS_BUCKET).create_signed_url(object_path, 3600)
    if isinstance(signed, dict):
        url = signed.get("signedURL") or signed.get("signedUrl")
    else:
        url = getattr(signed, "signed_url", None)
    if not url:
        raise RuntimeError("Could not create signed video download URL.")
    if url.startswith("/"):
        url = os.environ["SUPABASE_URL"].rstrip("/") + "/storage/v1" + url
    with requests.get(url, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        with open(destination, "wb") as out:
            for chunk in response.iter_content(8 * 1024 * 1024):
                if chunk:
                    out.write(chunk)

def upload_snapshot(client, user_id, frame, object_name):
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise RuntimeError("Could not encode snapshot.")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in object_name)[:40]
    path = f"{user_id}/{day}/video_{uuid.uuid4().hex}_{safe}.jpg"
    client.storage.from_(SNAPSHOTS_BUCKET).upload(
        path, encoded.tobytes(),
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )
    return path

def category_for(name):
    if name == "person":
        return "Human"
    if name in {"bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"}:
        return "Animals"
    if name in {"bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat"}:
        return "Vehicles"
    return "Others"

def severity_for(category):
    return {"Human": "warning", "Animals": "normal", "Vehicles": "normal"}.get(category, "normal")

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("smart-agri-supabase")],
    timeout=3600,
    cpu=2,
    memory=8192,
    max_containers=3,
)
def process_video_job(job_id: int, user_id: str):
    """Only a trusted backend may trigger this with user_id from verified auth."""
    from ultralytics import YOLO

    client = supabase_client()
    job = owned_job(client, job_id, user_id)
    if job.get("status") not in {"queued", "processing"}:
        return {"status": "ignored", "reason": "Job is not queued or processing."}
    object_path = job.get("video_path")
    if not object_path:
        raise RuntimeError("Job has no video_path.")
    if not object_path.startswith(f"{user_id}/") or ".." in Path(object_path).parts:
        raise RuntimeError("Video path is outside the owner's folder.")

    update_job(client, job_id, user_id, status="processing",
               started_at=job.get("started_at") or utc_now(), error_message=None)

    with tempfile.TemporaryDirectory(prefix="smart-agri-") as workdir:
        video_path = os.path.join(workdir, "input_video")
        download_video(client, object_path, video_path)
        model = YOLO("/root/models/yolov8n.pt")
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError("Could not open uploaded video.")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        update_job(client, job_id, user_id, total_frames=total_frames or None,
                   source_fps=fps or None, processed_frames=0,
                   progress_percent=0.0, event_count=0)

        rows = client.table("settings").select("*").eq("user_id", user_id).limit(1).execute().data
        cfg = rows[0] if rows else {}
        confidence = float(cfg.get("confidence_threshold", 0.25))
        iou = float(cfg.get("iou_threshold", 0.45))
        frame_skip = max(0, int(cfg.get("frame_skip", 2)))
        snapshots_enabled = bool(cfg.get("snapshot_on_event", True))
        alerts_enabled = bool(cfg.get("alerts_enabled", True))
        alert_human = bool(cfg.get("alert_on_human", True))
        alert_animal = bool(cfg.get("alert_on_animal", True))
        alert_vehicle = bool(cfg.get("alert_on_vehicle", False))

        frame_index = 0
        event_count = 0
        last_progress = time.monotonic()
        try:
            while True:
                current = owned_job(client, job_id, user_id)
                if current.get("cancel_requested"):
                    update_job(client, job_id, user_id, status="cancelled",
                               completed_at=utc_now(), processed_frames=frame_index,
                               progress_percent=min(99.0, frame_index * 100.0 / total_frames) if total_frames else 0.0)
                    return {"status": "cancelled", "processed_frames": frame_index}

                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                frame_index += 1
                if frame_skip and frame_index % (frame_skip + 1) != 0:
                    continue

                result = model(frame, conf=confidence, iou=iou, verbose=False)[0]
                for box in result.boxes:
                    cls_id = int(box.cls[0].item())
                    score = float(box.conf[0].item())
                    name = str(result.names[cls_id])
                    category = category_for(name)
                    bbox = [float(v) for v in box.xyxy[0].tolist()]
                    snapshot_path = None
                    if snapshots_enabled:
                        annotated = frame.copy()
                        x1, y1, x2, y2 = map(int, bbox)
                        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 200, 0), 2)
                        cv2.putText(annotated, f"{name} {score:.0%}", (x1, max(15, y1 - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 2)
                        snapshot_path = upload_snapshot(client, user_id, annotated, name)

                    now = utc_now()
                    event_id = str(uuid.uuid4())
                    payload = {
                        "source": "Video Analysis", "camera_id": None,
                        "camera_name": job.get("original_filename") or "Uploaded Video",
                        "video_analysis_job_id": int(job_id), "location": "Uploaded Video",
                        "user_id": str(user_id), "object_name": name, "category": category,
                        "severity": severity_for(category), "confidence": score,
                        "avg_confidence": score, "frame_count": 1,
                        "start_time": now, "end_time": now, "duration_seconds": 0.0,
                        "snapshot_path": snapshot_path, "bounding_box": bbox,
                        "metadata": {"frame_index": frame_index, "source_fps": fps},
                        "client_event_id": event_id,
                    }
                    inserted = client.table("detections").insert(payload).execute().data
                    if not inserted:
                        raise RuntimeError("Supabase did not return the inserted detection.")
                    detection = inserted[0]
                    event_count += 1

                    alert_enabled = alerts_enabled and (
                        (category == "Human" and alert_human)
                        or (category == "Animals" and alert_animal)
                        or (category == "Vehicles" and alert_vehicle)
                    )
                    if alert_enabled:
                        client.table("alerts").insert({
                            "detection_id": detection["id"], "camera_id": None,
                            "camera_name": job.get("original_filename") or "Uploaded Video",
                            "user_id": str(user_id), "category": category,
                            "severity": severity_for(category),
                            "alert_type": {"Human": "intrusion", "Animals": "animal_activity",
                                           "Vehicles": "vehicle_activity"}.get(category, "object_detected"),
                            "title": f"{category} Detected",
                            "message": f"{name.title()} detected in {job.get('original_filename') or 'uploaded video'}",
                            "client_alert_id": str(uuid.uuid4()),
                        }).execute()

                now_mono = time.monotonic()
                if now_mono - last_progress >= 1.0:
                    progress = min(99.0, frame_index * 100.0 / total_frames) if total_frames else 0.0
                    update_job(client, job_id, user_id, processed_frames=frame_index,
                               progress_percent=progress, event_count=event_count)
                    last_progress = now_mono

            update_job(client, job_id, user_id, status="completed",
                       processed_frames=frame_index, progress_percent=100.0,
                       event_count=event_count, completed_at=utc_now(), error_message=None)
            return {"status": "completed", "processed_frames": frame_index, "event_count": event_count}
        except Exception as exc:
            try:
                update_job(client, job_id, user_id, status="failed",
                           error_message=f"Processing/persistence failed: {type(exc).__name__}",
                           completed_at=utc_now())
            except Exception:
                pass
            raise
        finally:
            cap.release()


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("smart-agri-supabase")],
    timeout=120,
)
@modal.asgi_app()
def trigger_api():
    """Authenticated HTTP trigger for the Vercel frontend."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware

    web = FastAPI()
    # Authentication is enforced by validating the Supabase access token below.
    # CORS is not authentication; only verified user tokens can trigger jobs.
    web.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @web.post("/trigger")
    async def trigger(request: Request):
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing Supabase access token.")

        token = auth_header.split(" ", 1)[1].strip()
        try:
            body = await request.json()
            job_id = int(body.get("job_id"))
        except Exception:
            raise HTTPException(status_code=400, detail="A valid job_id is required.")

        try:
            client = supabase_client()
            user_response = client.auth.get_user(token)
            auth_user = getattr(user_response, "user", None)
            if not auth_user or not getattr(auth_user, "id", None):
                raise HTTPException(status_code=401, detail="Invalid or expired session.")

            user_id = str(auth_user.id)
            job = owned_job(client, job_id, user_id)

            if job.get("status") != "queued":
                raise HTTPException(status_code=409, detail="Job is not queued.")
            video_path = job.get("video_path")
            if not video_path or not video_path.startswith(f"{user_id}/"):
                raise HTTPException(status_code=400, detail="Job video path is invalid.")

            # User identity is derived from the verified token, never from request JSON.
            process_video_job.spawn(job_id, user_id)
            return {"status": "accepted", "job_id": job_id}
        except HTTPException:
            raise
        except Exception:
            # Avoid leaking service-role or database details to the browser.
            raise HTTPException(status_code=500, detail="Could not trigger video processing.")

    return web

@app.local_entrypoint()
def main():
    print("Modal video worker module loaded. Trigger it from a trusted backend.")
