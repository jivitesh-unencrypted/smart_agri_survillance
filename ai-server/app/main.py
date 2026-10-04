"""
FastAPI entrypoint for the LOCAL AI / CAMERA SERVER.

What runs here and only here:
  * OpenCV camera capture (webcam / USB / IP / RTSP / HTTP-MJPEG)
  * Ultralytics YOLO inference and bounding boxes
  * Snapshot generation
  * Alert/event generation
  * MJPEG live streaming and the WebSocket telemetry feed

What it does NOT do:
  * It never serves the UI (Cloudflare Pages does).
  * It never runs on Render and needs no persistent disk in the cloud -
    `storage/`, `data/` and the model all live on the user's machine.

It is also not required for read-only use of the dashboard: the frontend
reads detections, alerts, cameras and analytics straight from Supabase,
so browsing history and analytics works even with this server switched off.
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import outbox, supabase_client as sc
from app.api import (
    alerts,
    analytics,
    auth,
    cameras,
    detections,
    settings as settings_api,
    system,
    video_analysis,
)
from app.camera.stream_manager import StreamManager
from app.core.config import settings
from app.detection.registry import get_load_error, load_detector
from app.websocket.routes import router as websocket_router

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ai_server")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Local AI/camera server. Runs YOLO + OpenCV on your own machine, serves the live "
        "stream and WebSocket telemetry, and syncs detection events to Supabase. No cloud AI."
    ),
)

# The frontend is a Cloudflare Pages origin, so its URL must be allowed
# explicitly alongside the local dev server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error. Check the server logs."})


@app.on_event("startup")
async def on_startup():
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)

    # --- Local YOLO model ---
    load_detector()
    error = get_load_error()
    if error:
        logger.warning("YOLO model failed to load: %s", error)
        logger.warning("Camera streaming and detection are unavailable until this is fixed.")
    else:
        logger.info("YOLO model loaded from %s", settings.resolved_model_path)

    # --- Supabase ---
    if sc.is_configured():
        logger.info("Supabase configured for %s", settings.SUPABASE_URL)
        if sc.ping():
            logger.info("Supabase connection OK")
        else:
            logger.warning(
                "Supabase is configured but not reachable. Detection will continue locally "
                "and events will be queued until the connection returns."
            )
    else:
        logger.warning(
            "%s - running in fully local mode. The dashboard, detection logs, alerts and "
            "analytics still work (they read straight from Supabase in the browser), but "
            "detection events will not reach the cloud.",
            settings.supabase_placeholder_reason,
        )

    # --- Offline outbox ---
    outbox.init()
    app.state.sync_worker = outbox.SyncWorker()
    app.state.sync_worker.start()

    # --- Camera workers ---
    loop = asyncio.get_event_loop()
    app.state.stream_manager = StreamManager(loop)
    logger.info("AI server ready (CORS: %s)", ", ".join(settings.cors_origins_list))


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down - stopping all camera streams...")
    manager = getattr(app.state, "stream_manager", None)
    if manager is not None:
        manager.stop_all()

    # Flush anything still queued so a clean shutdown does not delay events.
    try:
        stats = outbox.flush()
        if stats["flushed"]:
            logger.info("Flushed %s queued event(s) on shutdown", stats["flushed"])
        if stats["remaining"]:
            logger.info("%s event(s) remain queued for the next start", stats["remaining"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not flush the outbox on shutdown: %s", exc)

    worker = getattr(app.state, "sync_worker", None)
    if worker is not None:
        worker.stop()


# ---------------------------------------------------------------- routers
app.include_router(auth.router)
app.include_router(cameras.router)
app.include_router(detections.router)
app.include_router(alerts.router)
app.include_router(analytics.router)
app.include_router(settings_api.router)
app.include_router(system.router)
app.include_router(video_analysis.router)
app.include_router(websocket_router)

# Snapshots are ALSO served locally, which is what keeps the app fully
# usable offline: while the outbox holds an event, its JPEG is still
# reachable through the local server. Once synced, the frontend prefers
# the Supabase Storage URL.
app.mount("/storage", StaticFiles(directory=str(settings.storage_dir)), name="storage")


@app.get("/")
def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "role": "Local AI/camera server (YOLO + OpenCV). The dashboard UI is served from Cloudflare Pages.",
    }
