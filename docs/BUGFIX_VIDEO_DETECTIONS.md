# Bug fix — video detections showed 0 and snapshots were missing

Two independent faults, one in **ownership** and one in **snapshot paths**, made
the Detections page look empty for everything produced by video-file analysis,
and made the snapshot thumbnails fail to load even for rows that *were*
visible.

Both are fixed in code, and the rows already in the live database were repaired
(detections, alerts and Storage objects).

---

## 1. Symptom

* **Bug 1** — Upload a video, watch it finish with `event_count = 14`, click
  **View** (which navigates to `/detections?source=Video Analysis`): the table
  is empty and **Total Events** reads `0`.
* **Bug 2** — Detections that *do* appear (live-camera rows) render no
  thumbnail: the Snapshot column is blank.

## 2. Root causes

### Bug 1 — every video-analysis row was written with `user_id = NULL`

`ai-server/app/repositories/_common.py::select_one()` applied
`.eq(column, value)` for **every** filter, including filters whose value was
`None`:

```python
for col, value in filters.items():
    q = q.eq(col, value)          # value may be None
```

`postgrest` serialises `None` as the literal string `null`, so
`jobs_repo.get_job(job_id)` — which omits the optional owner filter — sent

```
GET /rest/v1/video_analysis_jobs?select=*&user_id=eq.null&limit=1
```

PostgREST rejects that against a `uuid` column:

```
22P02  invalid input syntax for type uuid: "null"
```

`supabase_client.call()` turns a non-2xx response into `SupabaseUnavailable`,
and `video_analysis_service._run_job` caught exactly that exception and
**continued with `owner_id = None`**:

```python
try:
    job_row = jobs_repo.get_job(job_id) or {}
    owner_id = job_row.get("user_id")
except sc.SupabaseUnavailable:
    owner_id = None          # <-- always taken
```

So `CameraEventTracker(user_id=None)` produced rows whose insert payload had the
`user_id` key dropped entirely (`jsonable_payload` omits `None`), leaving
`detections.user_id IS NULL`.

RLS then does the rest:

```sql
using (auth.uid() = user_id)      -- NULL is never true
```

An unowned row is invisible to **every** signed-in user. The browser reads
`detections` straight from PostgREST with the anon key, so it got **0 rows**,
while the job row — read by the AI server with the service role, which bypasses
RLS — reported `event_count = 14`.

The same defect broke two other paths that omit the owner filter:

* `request_cancel()` / `_is_cancel_requested()` — cancelling a job always
  failed with the same 400.
* `create_alert_for_detection()` — see below.

`select_many()` already skipped `None` filters; only `select_one()` did not.

#### The alert half was a separate instance of the same mistake

`_event_to_dict()` — the dict handed to `create_alert_for_detection()` — did not
contain `user_id` at all, and `create_alert_for_detection` reads
`event.get("user_id")`. So **every** alert, live-camera ones included, was
written unowned. This is why the live database had `37 / 37` alerts with
`user_id IS NULL` even though most live-camera detections were owned correctly.

#### A data-integrity side effect

An unowned event that went through the offline outbox was stamped with the
*legacy* owner (the earliest-created profile) when it was flushed. Job 3
belongs to user `fda8d15b…`, yet one of its detections landed under
`097ed48d…` — a different account.

### Bug 2 — two independent snapshot-path faults

**(a) The event-*extension* branch dropped `user_id`.**

`process_detections()` has two branches. The *new event* branch passed the
owner; the *extension* branch did not:

```python
# extension of an existing event
snapshot = save_snapshot(annotated_frame_bgr, self.camera_id, name)   # no user_id
```

`_extend_detection()` then **overwrote** the row's `snapshot_path` with that
non-namespaced key, so an event's snapshot silently lost its owner folder the
first time the event was extended.

**(b) The Storage key repeated the bucket name.**

`storage.snapshot_object_path()` returned `snapshots/<user_id>/<day>/<file>`
and that string was used verbatim as the object key **inside the bucket already
named `snapshots`**, so `storage.objects.name` was
`snapshots/<user_id>/<day>/<file>`.

Migration `0005` sets the read policy to

```sql
(storage.foldername(name))[1] = auth.uid()::text
```

`name` is relative to the bucket root — the bucket's own name is **not** part
of it — and `storage.foldername()` drops the last segment, so `[1]` is the
first directory in the key. For `snapshots/<user_id>/...` that segment is the
literal string `snapshots`, never a user id, so the policy matched **0 of 37**
objects: `createSignedUrl()` failed for everybody and no snapshot could be
served from the cloud.

The only reason any thumbnail had ever worked is the local fallback
(`/storage/...`), which only resolved the un-namespaced legacy layout and only
while the AI server was running.

---

## 3. Files changed

### `ai-server/app/repositories/_common.py`
`select_one()` now skips `None` filter values, exactly like `select_many()`.
An omitted optional filter means "do not filter on this column". This is the
root-cause fix for Bug 1: `get_job(job_id)` now resolves the owner, and cancel
works.

### `ai-server/app/services/video_analysis_service.py`
`_run_job` no longer treats an unresolvable owner as a reason to continue. If
the job's owner cannot be read it marks the job **failed** with an explanatory
`error_message` instead of writing rows that RLS would hide from everyone. A
visible failure beats silently invisible data. (With the `select_one` fix this
branch should never be reached.)

### `ai-server/app/services/detection_service.py`
* The event-**extension** branch now passes `user_id=self.user_id` to
  `save_snapshot()` — fix (a) above.
* `save_snapshot()` writes the local JPEG to a path that **mirrors the Storage
  key exactly**, relative to `settings.storage_dir`. Because the AI server's
  `/storage` route serves that same root, the offline fallback in
  `SnapshotImage` now resolves owner-namespaced keys too, not just the legacy
  flat layout.
* `_event_to_dict()` now carries `user_id`, so `create_alert_for_detection()`
  can stamp ownership — this is what makes alerts owned.

### `ai-server/app/storage.py`
`snapshot_object_path()` returns `<user_id>/<day>/<filename>`: no `snapshots/`
prefix, because the bucket name is not part of the object key and the policy
requires the **first** segment to be the user id. No migration was needed —
this makes new writes satisfy the policy that is already applied.

### `frontend/src/lib/api.js`
* `db['/api/detections']` destructures `{ data, error, count }` instead of
  calling `unwrap()`. `unwrap()` returns only `data`, so the exact count was
  being discarded and `total` was set to `rows.length` — i.e. the *page size*.
  With `count: 'exact'` the real total now reaches the UI, so **Total Events**
  and the pagination are correct.
* Docstring corrected: the key layout is `<user_id>/...`, not
  `snapshots/<user_id>/...`.

### `frontend/src/pages/Detections.jsx`
Added an `error` state. The load path previously had `try/finally` with **no
`catch`**, so a failed request left the previous values on screen and the
empty state ("No detection records found") was rendered for what was actually
an API failure. Now loading, error and empty are three distinct states, and the
error state shows the reason with a **Retry** button.

### `ai-server/scripts/migrate_legacy_snapshots.py`
The re-keying tool had the same wrong prefix baked in. It now derives the
destination from the row's own owner — `"<user_id>/" + key-with-the-wrong-
prefix-stripped` — which repairs all three historic shapes:
`snapshots/<date>/<file>`, `snapshots/<uid>/<date>/<file>` and anything else.
It also used `getattr(client.storage, "from")`, which does not exist
(`SyncStorageClient` exposes `from_`); that is fixed and it uses the
server-side `move()`.

### `ai-server/scripts/backfill_detection_ownership.py` (new)
Re-attributes rows the bug orphaned. Ownership is derived, never guessed:

| row | owner taken from |
|---|---|
| `Video Analysis` detection | its `video_analysis_jobs.user_id` |
| `Live Camera` detection | its `cameras.user_id` |
| alert | its detection's owner |
| alert with no `detection_id` | the job id in its `camera_name` (`job<id>_<hex>.mp4`) |

Defaults to a dry run; `--apply` writes. Rows whose parent cannot be resolved
are reported and left alone.

### `ai-server/scripts/verify_two_user_isolation.py`
Its probe objects and expected `snapshot_path` used the buggy
`snapshots/<uid>/...` layout, so it would have asserted the wrong contract. Also
had the same `storage.from` vs `storage.from_` mistake. Both corrected.

### `README.md`
* The Storage-key bullet described the broken layout; corrected, with a
  warning not to reintroduce the prefix.
* The "per-user isolation is written but NOT yet active" banner was stale —
  migration `0005` **is** applied. Corrected; the stale banner is part of why
  the key-layout fault went unnoticed.

---

## 4. Database / Supabase changes

**No new migration was required and no applied migration was edited.**

Migration `0005` was already applied and already correct. The code was the part
that disagreed with it, so the fix was to make the code satisfy the existing
policy.

Two **data** repairs were run against the live project (both are repeatable and
idempotent):

1. `backfill_detection_ownership.py --apply`
   * 19 detections re-attributed: 18 that were `NULL` now carry the owner of
     the job (or camera) that produced them, plus one that had been stamped
     with the **wrong** account and was moved back to `fda8d15b…`.
   * all 37 alerts re-attributed (36 via their `detection_id`, 1 via the job id
     in its `camera_name`). `null_owner` went `37 → 0`.
2. `migrate_legacy_snapshots.py`
   * 37 Storage objects moved from `snapshots/...` to `<user_id>/<day>/<file>`
     and their `snapshot_path` rows updated in step. `moved=37 skipped=0
     failed=0`.

No change to `.env`, buckets, policies or keys was needed.

---

## 5. Restart

The AI server holds the changed Python code, so it must be restarted. The
frontend change also needs a rebuild (or a dev-server reload).

```bat
:: Terminal 1 - stop the running AI server (Ctrl+C), then:
cd ai-server
start.bat

:: Terminal 2 - stop the running frontend (Ctrl+C), then:
cd frontend
start.bat
```

If you prefer to run them directly:

```bat
cd ai-server
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

cd frontend
npm run dev
```

Confirm the server is up (should not report a Supabase problem):

```bat
curl http://localhost:8000/api/system/health
```

---

## 6. Manual test procedure

### A. Upload + analyse + View (the reported bug)

1. Open **http://localhost:5173** and sign in.
2. **Video Analysis** → choose an `.mp4` with people/animals/vehicles → Upload.
3. Wait for the job to reach `completed` and note its **Events** count.
4. Click **View**. The Detections page opens filtered to
   `source = Video Analysis`.

   * The table lists the detections.
   * **Total Events** equals the job's event count (or more, if you have
     earlier video-analysis events).
   * Each row's **Snapshot** cell shows the image.
5. Cross-check in Supabase:

   ```sql
   select id, source, user_id, video_analysis_job_id, snapshot_path
     from detections
    where source = 'Video Analysis'
    order by id desc;
   ```

   Every row must have a non-NULL `user_id` equal to your own, and a
   `snapshot_path` whose **first segment is that same uuid**.

### B. Snapshot exists in Storage and renders

6. Dashboard → **Storage** → bucket `snapshots`. The object for each row must
   exist at the exact `snapshot_path`, i.e. `<uuid>/YYYY-MM-DD/<file>.jpg`.
7. In the app, open the snapshot preview. The signed URL must return the JPEG
   (a thumbnail and the enlarged preview both load).

### C. Empty video → honest empty state

8. Upload a short video with nothing in it (e.g. a still wall).
9. The job finishes with `0` events; **View** shows
   *"No detection records found for the selected filters."* — the empty state,
   not a spinner and not an error.

### D. Failure states

10. Force the *request itself* to fail (make the machine offline, or point
    `VITE_SUPABASE_URL` at an unreachable host and restart the frontend), then
    open/reload Detections. It must show the **"Could not load detection
    records."** panel with the reason and a **Retry** button — never a silent
    `0` and never *"No detection records found"*.
    (Note: stopping only the *AI server* does not break this page — history
    comes straight from Supabase by design. Signing out is also not an error:
    RLS simply returns zero rows, which correctly shows the empty state.)
11. Upload an unsupported file (e.g. `.txt`): the upload is rejected with a
    visible message; no job appears.

### E. Ownership / isolation

12. Sign in as a second account. It must see **only** its own detections,
    alerts, cameras and jobs — and its **Total Events** must not include the
    first account's rows.
12. With the first account signed in, take a detection's `snapshot_path` and try
    to sign it from another account's session. The request must fail (RLS on
    `storage.objects`).

### E2. Automated isolation check (optional but recommended)

With the AI server running:

```bat
cd ai-server
.\venv\Scripts\python.exe scripts\verify_two_user_isolation.py
```

Exit code `0` means every check passed.

### E. Logs

12. AI server log (`ai-server` console / `ai-server/logs`) should show
    `Uploaded snapshot to snapshots/<uuid>/...` for new events and no
    `invalid input syntax for type uuid` errors.
13. Browser dev tools network tab: the `/rest/v1/detections` response carries a
    `Content-Range` header like `0-19/27`, and the signed-URL request returns
    `200`.

---

## 7. Verification performed on the live project

* The exact defect reproduced: `.eq("user_id", None)` →
  `APIError 22P02 invalid input syntax for type uuid: "null"`.
* After the fix: `get_job(1)` resolves the owner `097ed48d…`, a wrong owner
  still returns nothing, `get_camera`/`get_alert` without an owner work, and
  `_is_cancel_requested` returns a bool instead of raising.
* A throwaway Auth user drove the **real** `CameraEventTracker` pipeline end to
  end (created and deleted by the script): the detection row carried the right
  owner, its `snapshot_path` was owner-namespaced, the owner could read the row
  back **through RLS**, the alert produced for the event was visible to the
  owner, and the snapshot signed and downloaded as `image/jpeg`. 7/7 checks.
* Storage policy exercised with a real anon-key session: the new layout signs
  and downloads; the old `snapshots/<uid>/...` layout is **denied**; another
  user's object cannot be signed; `detections` RLS returns 0 rows. 7/7 checks.
* Final state: 0 unowned detections, 0 unowned alerts, every referenced
  `snapshot_path` starts with its owner's uuid and every referenced object
  exists in Storage, each job's detections carry the job's owner. 9/9 checks.
* Frontend `npm run build` succeeds; `import app.main` succeeds.

## 8. Remaining limitations

* **43 orphaned objects** are still in the bucket under the old `snapshots/`
  prefix. No row references them, so they are invisible in the UI and cannot be
  read by anyone; they are leftovers from snapshots that were *superseded* when
  an event was extended. Delete them from **Dashboard → Storage → `snapshots`**
  if you want the bucket clean — nothing in the app points at them, so removal
  is safe. The 37 objects that *are* referenced were moved to the correct
  `<uuid>/<day>/<file>` keys and are the ones the Detections page uses.

* **Snapshots are still superseded without cleanup.** Every
  `snapshot_interval_seconds` during an ongoing event a new JPEG is written and
  uploaded, and `snapshot_path` is overwritten — the previous object is left
  behind. That is where the 43 orphans came from. It is pre-existing behaviour,
  not introduced here, and it is *not* a correctness problem (the row always
  points at an object that exists), but the bucket will keep growing. The clean
  fix is to `delete_snapshot()` the superseded key after a successful upload, or
  to upload only on new events. Left alone deliberately, because it changes
  working, tested behaviour.

* **`detections.user_id` and `alerts.user_id` are nullable** in the live
  schema, even though migration `0005` intends them `NOT NULL` (the
  `set not null` only runs when `public.users` already has rows at migration
  time). That nullability is what let Bug 1 fail silently instead of loudly. A
  follow-up migration adding `NOT NULL` would turn any future regression into a
  hard error — recommended once you are satisfied no legacy rows remain. It was
  not added here because it is not required for the fix and would need every
  legacy row resolved first.

* **The outbox still has a legacy-owner fallback**
  (`outbox._ensure_owner` → "earliest profile"). It only triggers for a queued
  payload with no owner, which the fixes now prevent, but if it ever fires
  again it can misattribute data across accounts. Recommendation: when there is
  more than one profile, fail the replay loudly instead of guessing.

* **`supabase/migrations/0005_per_user_isolation.sql` still contains the
  outdated comment** `"snapshots/<user_id>/..."`. It was not edited because it
  is already applied; the correction lives in `storage.py`, `README.md` and the
  migration script.

* **The category KPI cards on the Detections page count the current page, not
  the whole result set.** "Total Events" is now the true server-side count, but
  "Human"/"Animals"/"Vehicles"/"Others" are still derived from the ≤20 rows on
  screen, so they can sum to less than the total. That mismatch pre-dates this
  fix and was made more visible by fixing `total`. Aligning them needs either
  four `count` queries or a server-side summary RPC.
