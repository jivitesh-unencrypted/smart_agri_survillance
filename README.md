# Smart Agri Surveillance — v3 (Cloudflare Pages + Supabase + local AI server)

Real-time YOLO surveillance for farms and small sites, split across three
pieces so that **no heavy work ever runs in the cloud**:

| Piece | Where it runs | What it does |
|---|---|---|
| **Frontend** | Cloudflare Pages (static) | React 18 + Vite + Tailwind. Reads detections/alerts/analytics straight from Supabase, streams video from the on-site server. |
| **Supabase** | Managed | Postgres (all records), Auth (accounts), Storage (event snapshots), RLS. |
| **AI server** | On the machine with the cameras | FastAPI + YOLO/OpenCV. Camera capture, inference, event grouping, snapshots, offline outbox. |

Render no longer hosts inference or storage. The frontend is static, the
database is managed Postgres, and the only server you run yourself is the
one that needs physical access to your cameras.

**Offline-first is the design, not a fallback.** If the internet drops, every
camera keeps running YOLO on the configuration you tuned, events queue on
local disk, and they push to Postgres when the link returns — without
duplicating rows.

---

## Table of contents

- [A. What changed — v2 → v3](#a-what-changed--v2--v3)
- [B. Project structure](#b-project-structure)
- [C. Architecture and data flow](#c-architecture-and-data-flow)
- [D. Prerequisites](#d-prerequisites)
- [E. Set up Supabase](#e-set-up-supabase)
- [F. Set up the AI server](#f-set-up-the-ai-server)
- [G. Set up and deploy the frontend](#g-set-up-and-deploy-the-frontend)
- [H. How to run](#h-how-to-run)
- [I. Configuration reference](#i-configuration-reference)
- [J. Offline-first behaviour](#j-offline-first-behaviour)
- [K. Security model](#k-security-model)
- [L. Troubleshooting, verification, known limitations](#l-troubleshooting-verification-known-limitations)

---

## A. What changed — v2 → v3

The v1 → v2 migration (Streamlit → React + FastAPI + SQLite) is preserved
intact: same pages, same routes, same detection logic, same severity and
title mapping, same camera management, same dashboard. v3 changes **where
things live**, not how they behave.

| Concern | v2 | v3 |
|---|---|---|
| Deployment | one app on Render | frontend → Cloudflare Pages, data → Supabase, AI → on-site |
| Database | SQLite + SQLAlchemy (`app/models/`, `core/database.py`) | Supabase Postgres via PostgREST; SQLAlchemy removed entirely |
| Auth | local JWT + bcrypt (`core/security.py`, `app/models/user.py`) | Supabase Auth; AI server verifies the Supabase JWT per request |
| Analytics | SQL aggregation in Python (`analytics_service.py`) | Postgres RPCs: `get_detection_summary`, `get_analytics`, `get_system_health` |
| Event snapshots | local files only | local files + Supabase Storage, with a local `/storage` fallback |
| Cloud outage | n/a | events queue in a local SQLite outbox and replay idempotently |
| Camera passwords | in the `cameras` row | `camera_secrets` table with **no RLS policies** (service-role only) |
| Settings reads | always from SQLite | Postgres, falling back to a local last-known-good cache when offline |
| Frontend data | all via the backend | Supabase directly (dashboard works with the AI server stopped) |
| Backend folder | `backend/` | `ai-server/` |

### What did *not* change

- **The YOLO model.** `models/yolov8n.pt` is untouched and still loaded from
  `MODEL_PATH`.
- **Every original column and entity name** — `cameras.camera_code`,
  `detections.avg_confidence`, `alerts.acknowledged`, the single-row
  `settings` table, and so on.
- **The category → severity → title mapping**: Human → `critical` /
  "Intrusion Alert" / `intrusion`, Animals → `warning` / "Animal Activity" /
  `animal_activity`, Vehicles → `info` / "Vehicle Activity" /
  `vehicle_activity`, Others → `normal` / "Object Detected" /
  `object_detected`.
- **Connection types**: `local_webcam`, `ip_camera`, `rtsp`, `http_mjpeg`.
- **Event-based grouping** (one row per object event, extended in place until
  a cooldown elapses) rather than one row per frame.
- **Open self-registration with equal access** for every account.
- Every page and route in the React app.

---

## B. Project structure

```
project/
├── supabase/
│   └── migrations/
│       ├── 0001_init_schema.sql       tables, indexes, triggers, generated columns
│       ├── 0002_rls_policies.sql      per-table policies (camera_secrets deliberately excluded)
│       ├── 0003_analytics_rpc.sql     get_detection_summary / get_analytics / get_system_health
│       └── 0004_storage_setup.sql     public-read `snapshots` bucket, upload = service-role only
│
├── ai-server/                         runs on the machine with the cameras
│   ├── app/
│   │   ├── main.py                    FastAPI entrypoint, startup/shutdown, /storage mount
│   │   ├── supabase_client.py         client factory, connectivity state, token verification
│   │   ├── outbox.py                  offline SQLite queue + background sync worker
│   │   ├── storage.py                 snapshot upload/delete
│   │   ├── api/                       auth, cameras, detections, alerts, analytics,
│   │   │                              settings, system, video_analysis + deps.py (auth guard)
│   │   ├── repositories/              one module per table (cameras, detections, alerts,
│   │   │                              settings, analytics, jobs, users) + _common.py
│   │   ├── services/                  business logic (event tracking, alerts, analytics,
│   │   │                              camera CRUD, settings, video analysis)
│   │   ├── schemas/                   Pydantic request/response models
│   │   ├── camera/                    capture, per-camera stream worker, manager,
│   │   │                              credentials, connection testing
│   │   ├── detection/                 YOLO wrapper + process-wide model singleton
│   │   ├── websocket/                 broadcast manager + /ws
│   │   └── core/config.py             env parsing and validation
│   ├── models/yolov8n.pt              your trained weights (unmodified)
│   ├── storage/                       snapshots/, recordings/, uploads/ (local cache)
│   ├── data/                          outbox.db, settings_cache.json, .camera_key
│   ├── requirements.txt
│   ├── .env.example / .env            git-ignored
│   ├── setup.bat / start.bat
│   └── venv/
│
├── frontend/                          deployed to Cloudflare Pages
│   ├── src/
│   │   ├── lib/supabase.js            browser client (anon key) + config error helper
│   │   ├── lib/api.js                 dual-source dispatcher (see section C)
│   │   ├── hooks/useAuth.jsx          Supabase Auth session provider
│   │   ├── hooks/useLiveSocket.js     WebSocket to the AI server
│   │   ├── components/SnapshotImage.jsx   Storage URL with local /storage fallback
│   │   ├── pages/                     Dashboard, LiveMonitoring, Cameras, Detections,
│   │   │                              Alerts, Analytics, VideoAnalysis, Settings, Login
│   │   └── layouts/, lib/utils.js, ...
│   ├── .env.example / .env            git-ignored
│   └── setup.bat / start.bat
│
└── README.md
```

---

## C. Architecture and data flow

```
   Browser (Cloudflare Pages)
        │
        ├── Supabase Auth ──────────────► sign in / register, holds the session JWT
        │
        ├── Supabase PostgREST ─────────► detections, alerts, cameras, settings
        │      (anon key + RLS)             └─ the dashboard renders with the AI server OFF
        │
        ├── Supabase Storage ───────────► snapshot images (public read)
        │
        └── AI server (LAN) ────────────► /ws live frames + telemetry
               │                          /api/cameras/{id}/stream  (MJPEG video)
               │                          start / stop / test, video-analysis upload
               │
               └── Supabase (service role) ─► writes detections, alerts, camera secrets
                    │                       └─ skips whenever unreachable; the outbox queues instead
                    └── YOLO/OpenCV + local disk ──► inference and snapshots
```

### Why the frontend reads Supabase directly

`src/lib/api.js` is a single dispatcher that sends each call to the right
place:

- **Supabase** for anything that is just a database read/write —
  detections, alerts, cameras list, settings, analytics. This is why the
  dashboard, detection history, alerts and analytics pages all work with the
  AI server completely stopped.
- **AI server** for anything that needs the hardware — video stream, camera
  start/stop/test, video-analysis upload, live WebSocket, camera writes
  (password handling needs the service role, which the browser must never
  have).

### Event lifecycle

1. The per-camera worker reads a frame, skips it if it isn't due for
   inference, and runs YOLO.
2. `CameraEventTracker` matches boxes to active instances by IoU. A new
   object starts an **event** (one UUID `client_event_id`, one row);
   subsequent frames extend that same event instead of adding rows.
3. The first frame of an event writes the detection row and raises an alert.
4. Later frames update the row in place, throttled to one write every
   `EVENT_UPDATE_THROTTLE_SECONDS` (3s) so a long event doesn't generate a
   network call per frame.
5. A snapshot JPEG is written to local disk at most every
   `snapshot_cooldown_seconds`; the one attached to a persisted event is
   uploaded to Supabase Storage.
6. Everything above happens identically whether or not Supabase is
   reachable — see section J.

---

## D. Prerequisites

- **Supabase project** (free tier is enough) — Project Settings → API for
  the URL and keys.
- **Python 3.10–3.12** for the AI server. 3.13 generally lacks prebuilt
  `torch` wheels on Windows; **3.11 is the safest choice**. `opencv-python-headless`
  is used, so no GUI dependency is pulled in.
- **Node.js 18+** and npm.
- A webcam, USB camera, or an RTSP/IP/HTTP-MJPEG camera on the network.
- Windows 10/11 for the `.bat` files; macOS/Linux work with the usual
  `python -m venv` / `npm run dev` substitutions.

---

## E. Set up Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** and run these files **in order**:
   - `supabase/migrations/0001_init_schema.sql`
   - `supabase/migrations/0002_rls_policies.sql`
   - `supabase/migrations/0003_analytics_rpc.sql`
   - `supabase/migrations/0004_storage_setup.sql`

   Each is idempotent (`create table if not exists`, `drop policy if exists`
   before `create policy`), so re-running is safe.
3. **Authentication → Providers → Email**: enable Email sign-in. Turn *off*
   "Confirm email" if you want accounts usable immediately (the app is
   designed for open self-registration).
4. **Project Settings → API** — copy out:
   - **Project URL** → `VITE_SUPABASE_URL` and `SUPABASE_URL`
   - **anon / publishable key** → `VITE_SUPABASE_ANON_KEY`
   - **service_role key** → `SUPABASE_SERVICE_ROLE_KEY` (server-side only!)

   The `service_role` key bypasses RLS entirely. Treat it exactly like a
   database password: it goes in `ai-server/.env` and **never** in the
   frontend, never in git, never in a screenshot.

---

## F. Set up the AI server

```bat
cd ai-server
setup.bat
```

This creates `venv\`, installs `requirements.txt`, and copies
`.env.example` to `.env`. Then edit `ai-server\.env`:

```ini
SUPABASE_URL=https://YOUR-PROJECT-REF.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...      # server-side only
SUPABASE_STORAGE_BUCKET=snapshots

SYNC_ENABLED=true
UPLOAD_SNAPSHOTS=true
SYNC_INTERVAL_SECONDS=15
OUTBOX_MAX_ATTEMPTS=8

MODEL_PATH=./models/yolov8n.pt
DEFAULT_CONFIDENCE_THRESHOLD=0.25
DEFAULT_IOU_THRESHOLD=0.45

CORS_ORIGINS=http://localhost:5173,https://your-project.pages.dev
HOST=127.0.0.1
PORT=8000
LOG_LEVEL=info
```

The server validates this file at startup. If `SUPABASE_URL` /
`SUPABASE_SERVICE_ROLE_KEY` are still the `.env.example` placeholders it
logs a clear warning and runs in **fully local mode** — detection, cameras
and the API all still work, events just queue instead of syncing. That is
intentional so you can run the app before creating the project.

---

## G. Set up and deploy the frontend

```bat
cd frontend
setup.bat
```

Copy `.env.example` to `.env` and fill in:

```ini
VITE_SUPABASE_URL=https://YOUR-PROJECT-REF.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOi...          # anon/publishable key ONLY
VITE_AI_SERVER_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws
```

> These are baked into the public bundle. Only the **anon** key belongs
> here. Never put the service-role key in this file.

### Deploy to Cloudflare Pages

- **Build command:** `npm run build`
- **Build output directory:** `dist`
- Set `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_AI_SERVER_URL`,
  `VITE_WS_URL` as Pages **environment variables** (Production and Preview).

After deployment, `VITE_AI_SERVER_URL` / `VITE_WS_URL` should point at the
**on-site** AI server (`http://<LAN-IP>:8000`), not `localhost` — the
browser resolves those, not the Pages host. Add the Pages URL to
`CORS_ORIGINS` in `ai-server/.env` and restart the AI server.

Because the frontend is static, it works offline once loaded; only the live
stream and camera controls need the AI server.

---

## H. How to run

```bat
:: Terminal 1 - AI server (leave running)
cd ai-server
start.bat

:: Terminal 2 - frontend (leave running)
cd frontend
start.bat
```

Then open **http://localhost:5173**, create an account from the **Create
account** tab, add a camera, click **Test**, then **Start**.

API docs: **http://localhost:8000/docs**

A useful first check, before opening the UI at all:

```bat
curl http://localhost:8000/api/system/health
```

`{"status":"ok"}` means the model loaded *and* Supabase is reachable.
`"status":"degraded"` still means detection is running — it reports that
the cloud is not configured/reachable, which in fully local mode is
expected.

---

## I. Configuration reference

### `ai-server/.env` (server-side, git-ignored)

| Variable | Default | Meaning |
|---|---|---|
| `SUPABASE_URL` | — | Project URL. Placeholders are detected and downgraded to local-only mode. |
| `SUPABASE_SERVICE_ROLE_KEY` | — | Service-role key. Server-side only. |
| `SUPABASE_STORAGE_BUCKET` | `snapshots` | Bucket for event snapshots. |
| `SYNC_ENABLED` | `true` | `false` = never contact the cloud; everything queues. |
| `UPLOAD_SNAPSHOTS` | `true` | `false` = snapshots stay 100% local; only metadata syncs. |
| `SYNC_INTERVAL_SECONDS` | `15` | How often the outbox retries. |
| `OUTBOX_MAX_ATTEMPTS` | `8` | Attempts before an event is dropped (logged loudly). |
| `MODEL_PATH` | `./models/yolov8n.pt` | Point at a different `.pt` if you retrain. |
| `DEFAULT_CONFIDENCE_THRESHOLD` | `0.25` | Used until the `settings` row exists. |
| `DEFAULT_IOU_THRESHOLD` | `0.45` | Event-grouping IoU. |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated allowed frontend origins. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address. Use `0.0.0.0` to reach it from the LAN. |
| `LOG_LEVEL` | `info` | `debug` logs every Supabase call. |

### `frontend/.env` (public bundle, git-ignored)

| Variable | Meaning |
|---|---|
| `VITE_SUPABASE_URL` | Supabase project URL. |
| `VITE_SUPABASE_ANON_KEY` | Anon/publishable key only. |
| `VITE_AI_SERVER_URL` | Base URL of the on-site AI server. |
| `VITE_WS_URL` | WebSocket URL, e.g. `ws://192.168.1.50:8000/ws`. |

### Runtime settings (Settings page → single `settings` row, id = 1)

`confidence_threshold`, `iou_threshold`, `inference_interval_ms`,
`frame_skip`, `event_cooldown_seconds`, `alerts_enabled`, `sound_enabled`,
`alert_on_human`, `alert_on_animal`, `alert_on_vehicle`,
`snapshot_on_event`, `max_snapshot_age_days`, `sync_enabled`,
`upload_snapshots_to_cloud`, `stream_max_width`, `model_path`.

`inference_interval_ms` and `frame_skip` are the two knobs that matter for
CPU load: interval is the minimum gap between inferences, and `frame_skip=N`
only infers on every (N+1)th frame. `stream_max_width` downscales frames
before inference on wide cameras.

Saves are **partial**: updating one field leaves the others alone.

---

## J. Offline-first behaviour

This is the part that distinguishes v3 from a thin cloud wrapper.

| Situation | What happens |
|---|---|
| Cloud unreachable, camera already running | Detection continues on your **tuned settings** (served from `data/settings_cache.json`), events and alerts are appended to the local outbox. |
| Cloud comes back | The outbox drains automatically, uploads any queued snapshots, and resolves each alert's `detection_id`. |
| The outbox is replayed twice | Nothing duplicates. Each event carries a UUID `client_event_id` / `client_alert_id` with a unique index, so `INSERT … on conflict` returns the existing row. |
| Settings row unreachable | Last-known-good settings from disk; built-in defaults only if that cache is also missing. |
| AI server stopped entirely | The dashboard, detection history, alerts, analytics and camera list still work — the browser reads them from Supabase. Only video, camera control and video analysis need the server. |

`GET /api/system/health` is honest about this:

```json
{"status":"degraded","model_loaded":true,"cloud_configured":true,
 "cloud_online":false,"pending_sync_events":2}
```

`cloud_online` is based on a *recent successful probe*, not on "the cloud
was reachable once since boot" — a link that died an hour ago reports
`false` rather than lying with `true`.

### An event that *ends* during an outage

Intermediate duration bumps are dropped while offline — they are redundant,
because the numbers you read are the closing ones. The closing write is not
dropped. It is queued as a `detection_final` row holding the complete event,
and replayed as `INSERT … ON CONFLICT (client_event_id) DO UPDATE`, so it
refreshes the existing row whether or not the event ever reached the cloud.

This is the case that is easy to get wrong: an event whose last frame arrives
offline never extends again, so if the closing write is simply swallowed the
row keeps the `frame_count` and `duration_seconds` from the single frame it
opened on — permanently wrong, and quietly so.

**Known gap:** while the cloud is down you cannot **add or edit cameras**,
because verifying a login requires Supabase Auth. Already-running cameras
keep working. Caching the camera list locally would close this; see section L.

---

## K. Security model

**The service-role key never leaves the AI server.** The browser only ever
gets the anon key, and every browser query runs through RLS.

- **RLS is on for every table.** Signed-in users get full access to
  `users`, `cameras`, `detections`, `alerts`, `settings`,
  `video_analysis_jobs` and `camera_status_history` — this preserves v2's
  "every account has equal access" model exactly.
- **`users` is narrower on purpose**: you can select your own row and
  insert/update only yourself.
- **`camera_secrets` has no policies at all.** With RLS enabled and zero
  policies, the anon and authenticated keys cannot read it *or* write it.
  Only the service-role key can, which is exactly how the AI server stores
  camera passwords. Passwords are also stripped out of `stream_url` before
  the camera row is saved, and the API returns `has_password: true` instead
  of the value.
- **Snapshots are public-read.** The bucket is public so an `<img src>` can
  render without a backend round-trip, but writes are service-role only.
- **Storage keys** are `snapshots/YYYY-MM-DD/cam<id>_<object>_<HHMMSS>.jpg`
  in a bucket named `snapshots`; `detections.snapshot_path` stores that key,
  and the frontend builds the public URL from it.
- **No frames are ever uploaded.** Only the small JPEG attached to a
  detection *event*; raw video never leaves the machine.
- **`/api/cameras/{id}/stream`** is intentionally unauthenticated because a
  browser `<img>` tag cannot send an Authorization header. Restrict it at
  the firewall/router if the LAN isn't fully trusted.

---

## L. Troubleshooting, verification, known limitations

### Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `status: "degraded"`, `cloud_configured: false` | `.env` still has the `.env.example` placeholders. Copy the real project URL and service-role key. |
| `401` from every API call | Frontend env not set, or you're hitting the AI server without a Supabase session. Check `VITE_SUPABASE_*` and that the browser has a session. |
| `401` specifically on camera create/update | Normal while the cloud is down — password writes need the service role. |
| `pip install` fails on `torch` | Use Python 3.11. On Windows you may need the CPU wheel first: `pip install torch --index-url https://download.pytorch.org/whl/cpu`. |
| Webcam won't open | Close Zoom/Teams/other tabs using it; try another device index; check Windows camera privacy. |
| RTSP times out | Confirm the URL in VLC first; many cameras want the **sub**stream URL for usable latency. |
| CORS error in the browser console | Add the frontend's origin to `CORS_ORIGINS` in `ai-server/.env` and restart the server. |
| Snapshots 404 in the UI | Normal when the cloud is down — `SnapshotImage.jsx` falls back to the AI server's `/storage` mount. |
| Live feed works but no boxes | Raise/lower thresholds in Settings; confirm `model_loaded: true` at `/api/system/status`. |
| `pending_sync_events` climbing | The cloud is unreachable — see `/api/system/health` for `last_error`. |
| Model fails to load | Check `models/yolov8n.pt` exists and `ultralytics`/`torch` installed; the startup log has the exact error. |

### What was verified

Run end-to-end against a local mock of Supabase (PostgREST, Storage and
Auth), plus a mock MJPEG camera, all offline-testable:

- ✅ All 29 routes register and respond; every endpoint returns a sensible
  status, with **no 500s**.
- ✅ Detection event grouping: 6 consecutive frames of one object → 1 new
  event + 5 in-place extensions + 1 alert.
- ✅ Live pipeline against a real MJPEG source: WebSocket delivers
  `camera_status`, `detection`, `alert`, `detection_count` and `fps`;
  camera status/FPS/latency persist to Postgres.
- ✅ Video analysis: upload → queued → processing → completed, 40/40 frames,
  progress 0→100 %, with detections and a linked alert.
- ✅ Filtering and pagination on `/api/detections` and `/api/alerts`
  (12/12 cases, including `page`/`page_size`, `category`, `severity`,
  `source`, `resolved`).
- ✅ Settings: partial saves merge into the single row and never create a
  second one.
- ✅ Camera CRUD: `stream_url` credentials stripped, password written to
  `camera_secrets` (obfuscated), password preserved on blank-password
  update, secret deleted with the camera.
- ✅ Alert lifecycle: `open → acknowledged → resolved` via the generated
  `status` column, with `resolved_at` stamped.
- ✅ Offline: cloud killed mid-run → detection continues on cached tuned
  settings → detection + alert queue locally → network restored → outbox
  drains to 0 with alerts' `detection_id` correctly resolved and snapshots
  uploaded.
- ✅ Idempotent replay for both detections and alerts.
- ✅ Frontend `npm run build` succeeds.

### Known limitations

- **Camera CRUD requires the cloud.** Creating or editing a camera while
  offline returns 401, because verifying the session needs Supabase Auth.
  Running cameras are unaffected. A local camera cache would fix this.
- **Snapshots captured but not attached to a persisted event stay local.**
  Only the snapshot on the frame that writes/updates an event is uploaded,
  which keeps cloud storage small; the rest remain in `storage/snapshots/`
  and are served by the AI server.
- **Video uses MJPEG-over-HTTP**, not WebRTC. Deliberate: simple, reliable
  behind plain HTTP, at the cost of bandwidth and no adaptive bitrate.
- **Recording is scaffolded only.** `storage/recordings/` exists but no
  recording feature is implemented — the original app had none either.
- **`max_snapshot_age_days` is stored but not enforced** by a background
  job. Old snapshots accumulate until you delete them.
- **The dashboard needs Supabase reachable.** The frontend reads records
  directly from PostgREST; only the live stream and camera control degrade
  gracefully when the AI server is down.
- **Mock-tested, not Supabase-tested.** Everything above ran against a local
  stand-in for PostgREST/Storage/Auth. The SQL itself has not been executed
  against a live Supabase instance in this environment — run the migrations
  in section E first and confirm before relying on it.