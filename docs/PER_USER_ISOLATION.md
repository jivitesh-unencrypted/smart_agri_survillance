# Per-user data isolation — rollout runbook

Everything in this document is for **you** to run. Nothing here has been
applied to your live Supabase project.

---

## 0. What state things are in right now

| Thing | State |
| --- | --- |
| Migration `0005_per_user_isolation.sql` | Written, **not applied** to your project |
| Its SQL | Executed and asserted against a real Postgres engine — 71 assertions, all passing (`supabase/tests/`) |
| AI server ownership code | Written, compiles, all 52 modules import |
| Two test accounts | **Created** in your project (see §3) |
| Legacy snapshot objects | Not yet moved (see §5) |
| AI server process | Running the *old* code — restart it after applying the migration (§6) |

Until §1 is done, the two accounts share all data exactly as before. That is
expected: the fix is a schema change plus application code, and neither takes
effect on its own.

---

## 1. Apply the migration

Pick **one** of these. All three run the same file.

### Option A — Supabase Studio (no tooling needed)

1. Open your project → **SQL Editor** → **New query**
2. Paste the entire contents of
   `supabase/migrations/0005_per_user_isolation.sql`
3. **Run**

### Option B — Supabase CLI

```bash
cd project
supabase link --project-ref qdsewsmcfluohagnthbz
supabase db push
```

### Option C — psql

```bash
psql "postgresql://postgres:<password>@db.qdsewsmcfluohagnthbz.supabase.co:5432/postgres" \
     -v ON_ERROR_STOP=1 \
     -f "supabase/migrations/0005_per_user_isolation.sql"
```

`-v ON_ERROR_STOP=1` matters: without it psql keeps going after an error and
you end up with a half-applied migration.

### Verify it landed

```sql
-- expect 6 rows
select table_name from information_schema.columns
where table_schema = 'public' and column_name = 'user_id'
order by table_name;

-- expect 0 rows (no blanket policies left)
select tablename, policyname, qual from pg_policies
where schemaname = 'public' and qual = 'true';

-- expect public = false
select name, public from storage.buckets where name = 'snapshots';
```

---

## 2. What the migration does, in order

| § | Change |
| --- | --- |
| 1 | Adds `user_id uuid references auth.users(id) on delete cascade` to `cameras`, `detections`, `alerts`, `video_analysis_jobs`, `camera_status_history` |
| 2 | `settings` loses its global `id` column and the `settings_single_row` check; primary key becomes `user_id`. The single `id = 1` row is copied to its owner's row. |
| 3 | **Legacy backfill** — every pre-existing row gets an owner (§4 below) |
| 4 | `cameras.camera_code` becomes unique **per user** instead of globally |
| 5 | Seven indexes so every owner-scoped read filters on `user_id` |
| 6 | Replaces every `using (true)` policy with `auth.uid() = user_id` |
| 7 | `snapshots` bucket becomes **private**; read policy keys on `storage.foldername(name)[1] = auth.uid()::text` |
| 8 | Re-creates `get_detection_summary`, `get_analytics`, `get_system_health` with a `p_user_id` parameter |

### Why §8 replaces the functions rather than editing them

In Postgres, changing a function's parameter list creates a *new* function.
`get_analytics()` and `get_analytics(uuid)` are two different functions, so the
old signature has to be dropped explicitly — otherwise the old global one
survives and stays callable.

Every one of the three now filters on:

```sql
coalesce(p_user_id, auth.uid()) = user_id
```

`p_user_id` exists **because the AI server uses the service-role key, which
bypasses RLS entirely.** For a browser call `p_user_id` is null and
`auth.uid()` does the work. For the AI server `auth.uid()` is null (a
service-role JWT carries no user `sub`) so a caller that forgets to pass
`p_user_id` gets **nothing** rather than everything. It fails closed.

---

## 3. The two test accounts

Already created in your project:

| | Email | Password |
| --- | --- | --- |
| A | `isolation-a@test.local` | `Isolation-A-2026!` |
| B | `isolation-b@test.local` | `Isolation-B-2026!` |

They are ordinary accounts and are left in place so the test is repeatable.
The test script deletes its own generated rows after each run but **not** the
accounts.

---

## 4. How existing data was assigned

Nothing was deleted. Every pre-existing row was **given an owner**: the
earliest-created row in `public.users`.

```sql
-- what the backfill resolves to, for your data:
select id, email, created_at from public.users order by created_at limit 1;
```

That is `bhaskarkuntumalla@gmail.com`
(`d6f65b74-8fad-495b-8511-2a2867b6246b`, created `2026-10-04T09:20:31Z`).

**This is correct for your data, and here is the evidence:**

| Row | Created | Owner assigned |
| --- | --- | --- |
| `users` A | `09:20:31Z` | — |
| camera 1 | `09:21:30Z` | A |
| video job `VID-20260803-WA0005.mp4` | `09:22:18Z` | A |
| first detection | `09:21:48Z` | A |
| last detection | `16:15:16Z` | A |
| `users` B | `16:16:26Z` | — |

Every existing camera, detection, alert and job was created after A signed up
and before B did. The other account (`bhaskar3246502@gmail.com`) registered
after the last detection, so nothing could have belonged to them.

### Reversing it

The assignment is recorded, not guessed, so it can be undone:

```sql
-- see the current assignment
select 'cameras' t, user_id, count(*) from public.cameras group by user_id
union all select 'detections', user_id, count(*) from public.detections group by user_id
union all select 'alerts', user_id, count(*) from public.alerts group by user_id
union all select 'video_analysis_jobs', user_id, count(*) from public.video_analysis_jobs group by user_id;

-- move everything back to A, if you disagree with the decision
update public.cameras set user_id = 'd6f65b74-8fad-495b-8511-2a2867b6246b';
update public.detections set user_id = 'd6f65b74-8fad-495b-8511-2a2867b6246b';
update public.alerts set user_id = 'd6f65b74-8fad-495b-8511-2a2867b6246b';
update public.video_analysis_jobs set user_id = 'd6f65b74-8fad-495b-8511-2a2867b6246b';
```

---

## 5. Move the existing snapshots into an owner's folder

The 27 legacy detections point at flat paths like
`snapshots/2026-10-04/cam1_person_145147_042583.jpg`. Once the bucket is
private those paths no longer match the policy, which requires the first path
segment to be the owner's uuid.

**Run this after §1** — it needs `detections.user_id` to exist.

```bash
cd ai-server

# see what would move, change nothing
.\venv\Scripts\python.exe scripts\migrate_legacy_snapshots.py --dry-run

# actually move them
.\venv\Scripts\python.exe scripts\migrate_legacy_snapshots.py
```

The script copies each object to `snapshots/<owner_uuid>/<date>/<file>`,
rewrites `detections.snapshot_path`, then removes the old object. It refuses to
run if any detection still has no owner.

---

## 6. Restart the AI server

The running process predates the ownership changes. After applying §1:

```bash
cd ai-server
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## 7. The two-user isolation test

```bash
cd ai-server
.\venv\Scripts\python.exe scripts\verify_two_user_isolation.py
```

Exit `0` means every check held. Exit `1` lists what failed.

### What it checks, and which boundary each one proves

Three separate boundaries can fail independently, so the test exercises all
three rather than just the database.

**A — Row-level security.** Every read and write uses the **anon** key with
that account's real access token, exactly as the browser does. RLS is the only
thing standing between the accounts, so a pass here means the *database* is
doing the work.

1. A creates Camera A; it is stamped with A's uuid
2. A creates Detection A and Alert A
3. B signs in and sees **zero** rows in `cameras`, `detections`, `alerts`
4. B cannot read Camera A by guessing its id
5. B's rename of Camera A has no effect
6. B cannot insert a camera owned by A
7. B creates Camera B — and gets **its own** `CAM-001`, proving the code is
   per-user now
8. A signs back in: sees its own camera, never B's
9. A's analytics count exactly A's detections; B's exclude them entirely
10. A's settings row and B's are different rows with different values
11. B cannot see A's video-analysis job; A can
12. Each account can sign a URL for its own snapshot and not the other's
13. A's session survives a "page refresh" (token reuse) and still sees only A

**B — The AI server.** It runs on the **service-role** key, which *bypasses*
RLS, so it has to enforce ownership itself. These checks hit the live routes
over HTTP:

14. `GET /api/cameras` with A's token returns A's camera and not B's
15. `GET /api/cameras` with B's token returns B's camera and not A's
16. `GET /api/cameras/{A's id}` with **B's** token → `404`, not `403`
17. `GET /api/cameras/{A's id}` with **A's** token → `200`
18. B cannot open A's MJPEG stream
19. `PATCH /api/alerts/{A's alert}` as **B** → `404`; as **A** → `200`
20. `GET /api/video-analysis/jobs/{A's job}` as **B** → `404`
21. An unauthenticated `GET /api/cameras` is rejected

**C — Storage.** Covered by checks 12.

### Cross-user access returns 404, not 403

A `404` deliberately does not confirm the row exists. A `403` would tell a
stranger "that id is real, you just may not have it", which is an existence
oracle.

### If the AI server is not running

The B-boundary checks are skipped with a visible `SKIP` line rather than
silently passing, and the script still exits non-zero if anything else fails.
Force this behaviour with `--skip-ai-server`.

### Manual equivalent, in the browser

1. Sign in as A, note Camera A's code (`CAM-001`) and its id in the URL
2. Sign in as B in a private window — B sees no cameras
3. B tries to open A's camera id directly → not found
4. B creates a camera and gives it the code `CAM-001` — succeeds
5. Sign back in as A — A sees its camera, and only its camera

---

## 8. Re-validating the migration itself

The migration SQL is executable and its isolation guarantees are asserted by a
test that runs against a real Postgres engine, independent of your live
project:

```bash
cd project/supabase/tests
npm install
npm test
```

This applies `0001`–`0005` in order, seeds the pre-isolation state, proves
that state really is shared, applies `0005`, and then asserts isolation for two
accounts. See `supabase/tests/README.md`.

---

## 9. If something goes wrong

**A route 500s with `column "user_id" does not exist`.**
Migration §1 was not applied. Re-run §1.

**A route 500s with `column "id" does not exist` on `settings`.**
The settings rework (§2) did not apply. Re-run §1.

**A snapshot 403s / `StorageException`.**
§5 has not been run, or the bucket is public but the object is still at a flat
path. Run §5's dry-run first.

**Analytics show 0 for a user who clearly has detections.**
The analytics repo needs `p_user_id`. Confirm `analytics.py` passes it, and
that §8 of the migration applied (`\df public.get_analytics`).

**You disagree about who owns the legacy rows.**
See the reversal SQL in §4. Nothing was deleted, so this is safe.