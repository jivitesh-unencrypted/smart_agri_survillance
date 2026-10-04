"""
Central configuration for the LOCAL AI / CAMERA SERVER, loaded from
environment variables / .env.

Architecture note
-----------------
This process is the only place YOLO, OpenCV, torch and camera access
ever run. It runs on the user's own machine (never on Render/Cloudflare),
talks to Supabase for persistence/auth/storage using the service-role
key, and serves the live MJPEG feed + WebSocket telemetry to the
frontend. Nothing secret in this file is ever shipped to the browser.
"""
from pathlib import Path
import re
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent  # ai-server/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    # --- App ---
    APP_NAME: str = "Smart Agri Surveillance AI Server"
    APP_VERSION: str = "3.0.0"

    # --- Security ---
    # Retained so an existing .env keeps working and so anything still
    # expecting a signed token keeps functioning. User authentication
    # itself is now delegated to Supabase Auth (see SUPABASE_* below);
    # there is no local password database any more.
    JWT_SECRET_KEY: str = "change-this-to-a-random-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # --- Supabase (server-side only - NEVER exposed to the frontend) ---
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "snapshots"

    # --- Cloud sync / offline behaviour ---
    SYNC_ENABLED: bool = True
    UPLOAD_SNAPSHOTS: bool = True
    SYNC_INTERVAL_SECONDS: int = 15
    OUTBOX_MAX_ATTEMPTS: int = 8

    # --- AI Model ---
    MODEL_PATH: str = "./models/yolov8n.pt"
    DEFAULT_CONFIDENCE_THRESHOLD: float = 0.25
    DEFAULT_IOU_THRESHOLD: float = 0.45

    # --- CORS: the frontend origin(s) allowed to call this local server ---
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Local server bind address ---
    # 0.0.0.0 is what you want once the frontend is on Cloudflare Pages and
    # other devices on the LAN need to reach this server.
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    LOG_LEVEL: str = "info"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def supabase_configured(self) -> bool:
        """
        True only when BOTH credentials look real.

        The `.env.example` placeholders (`https://YOUR-PROJECT-REF.supabase.co`
        and `eyJhbGciOi...`) are deliberately treated as "not configured", so
        copying the example file and running `start.bat` boots the server in
        honest local-only mode with a clear warning instead of crashing on a
        nonsense request.
        """
        url = (self.SUPABASE_URL or "").strip()
        key = (self.SUPABASE_SERVICE_ROLE_KEY or "").strip()
        if "YOUR-PROJECT" in url.upper():
            return False
        # https for a real hosted project; plain http is only tolerated for a
        # local Supabase stack (e.g. `supabase start` on 127.0.0.1).
        if url.startswith("https://"):
            pass
        elif url.startswith("http://") and re.search(r"://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/?$", url):
            pass
        else:
            return False
        # A Supabase key is a JWT: three dot-separated base64url segments.
        return key.count(".") == 2 and len(key) > 40

    @property
    def supabase_placeholder_reason(self) -> str:
        """Human-readable explanation when `supabase_configured` is False."""
        if not (self.SUPABASE_URL or "").strip():
            return "SUPABASE_URL is not set in ai-server/.env"
        if not (self.SUPABASE_SERVICE_ROLE_KEY or "").strip():
            return "SUPABASE_SERVICE_ROLE_KEY is not set in ai-server/.env"
        return (
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY still look like the .env.example "
            "placeholders - copy your real Project URL and service_role key from "
            "Supabase > Project Settings > API into ai-server/.env"
        )

    @property
    def sync_enabled(self) -> bool:
        """
        False means "never touch the cloud": detection keeps running and
        events pile up in data/outbox.db for later.
        """
        return bool(self.SYNC_ENABLED) and self.supabase_configured

    @property
    def upload_snapshots_to_cloud(self) -> bool:
        """
        Independent of SYNC_ENABLED on purpose. Setting this to False keeps
        100% of the JPEG bytes on the local machine while still syncing the
        small event metadata rows - useful when the internet link is slow
        or metered.
        """
        return bool(self.UPLOAD_SNAPSHOTS)

    # --- Paths (always resolved relative to the ai-server/ folder so the
    # app behaves the same regardless of the working directory) ---
    @property
    def storage_dir(self) -> Path:
        return BASE_DIR / "storage"

    @property
    def snapshots_dir(self) -> Path:
        return self.storage_dir / "snapshots"

    @property
    def recordings_dir(self) -> Path:
        return self.storage_dir / "recordings"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def model_dir(self) -> Path:
        return BASE_DIR / "models"

    @property
    def data_dir(self) -> Path:
        """Local-only state: offline outbox, camera credential key."""
        return BASE_DIR / "data"

    @property
    def outbox_db_path(self) -> Path:
        return self.data_dir / "outbox.db"

    @property
    def resolved_model_path(self) -> Path:
        p = Path(self.MODEL_PATH)
        if p.is_absolute():
            return p
        return (BASE_DIR / p).resolve()


settings = Settings()

# Ensure required directories exist on import (mirrors the original
# project's config.py behaviour of creating folders up front).
for folder in (settings.storage_dir, settings.snapshots_dir, settings.recordings_dir,
               settings.uploads_dir, settings.model_dir, settings.data_dir):
    folder.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------
# Detection category mapping (preserved from the original project)
# ------------------------------------------------------------------
CATEGORIES = {
    "Human": ["person"],
    "Animals": ["bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"],
    "Vehicles": ["bicycle", "car", "motorcycle", "bus", "truck", "train", "boat"],
}
CATEGORY_ORDER = ["Human", "Animals", "Vehicles", "Others"]

SEVERITY_MAP = {
    "Human": "critical",
    "Animals": "warning",
    "Vehicles": "info",
    "Others": "normal",
}

CATEGORY_BOX_COLOR_BGR = {
    "Human": (0, 255, 0),
    "Animals": (0, 165, 255),
    "Vehicles": (255, 0, 0),
    "Others": (128, 128, 128),
}

# Connection types a camera can be configured with (kept identical to
# the original app's vocabulary so migrated camera data lines up).
CONNECTION_TYPE_LOCAL = "local_webcam"
CONNECTION_TYPE_IP = "ip_camera"
CONNECTION_TYPE_RTSP = "rtsp"
CONNECTION_TYPE_HTTP = "http_mjpeg"

CONNECTION_TYPES = {
    CONNECTION_TYPE_LOCAL: "Local Webcam",
    CONNECTION_TYPE_IP: "IP Camera",
    CONNECTION_TYPE_RTSP: "RTSP Stream",
    CONNECTION_TYPE_HTTP: "HTTP/MJPEG Stream",
}
NETWORK_CONNECTION_TYPES = {CONNECTION_TYPE_IP, CONNECTION_TYPE_RTSP, CONNECTION_TYPE_HTTP}

# Reconnect / event-grouping tuning (also user-configurable via Supabase
# `settings`, which is polled continuously by each camera worker).
CAMERA_RECONNECT_ATTEMPTS = 5
DEFAULT_INFERENCE_INTERVAL_MS = 400          # run YOLO at most every N ms per camera
DEFAULT_EVENT_COOLDOWN_SECONDS = 8           # gap allowed before an event is considered "ended"
DEFAULT_SNAPSHOT_COOLDOWN_SECONDS = 5        # min seconds between snapshots for the same event

# A continuing event's row is extended at most this often. Every write is
# now a network round-trip to Postgres, so extending on every processed
# frame (as the original SQLite version did) would be needlessly chatty.
# The final update is always written un-throttled when the event closes.
EVENT_UPDATE_THROTTLE_SECONDS = 3

# Settings are re-read from Supabase this often. This is one tiny REST
# call per camera per interval - negligible, and it means changing a
# threshold in the UI takes effect without restarting the AI server.
SETTINGS_REFRESH_SECONDS = 10
