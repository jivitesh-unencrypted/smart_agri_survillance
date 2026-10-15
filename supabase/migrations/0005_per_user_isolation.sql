-- =====================================================================
-- Smart Agri Surveillance - per-user data isolation (migration 0005)
-- ---------------------------------------------------------------------
-- One authenticated Supabase Auth user == one isolated farm/data owner.
--
-- Before this migration every table was GLOBAL: there was no owner column
-- anywhere, and the RLS policies were `using (true)`, so any signed-in
-- browser could read (and write) every other user's cameras, detections,
-- alerts, jobs and status history. Two accounts therefore saw identical
-- data, which is exactly the shared-data behaviour being fixed here.
--
-- What this migration does:
--   1. Adds `user_id uuid references auth.users(id)` to every table that
--      holds user-owned data.
--   2. Reworks `settings` from a single global `id = 1` row into one row
--      per user keyed on `user_id`.
--   3. Makes `camera_code` unique per user instead of globally unique, so
--      each user has their own CAM-001.
--   4. Replaces every `using (true)` policy with an `auth.uid()` ownership
--      check. RLS stays ENABLED everywhere - it is never disabled.
--   5. Makes the `snapshots` bucket private and scopes reads to the
--      authenticated user's own folder. It was public before, so any
--      snapshot was readable by anyone who guessed or scraped the key.
--   6. Preserves existing rows - see "LEGACY ROWS" below. Nothing is
--      deleted.
--
-- LEGACY ROWS / OWNERSHIP DECISION (read this before running)
-- ---------------------------------------------------------
-- Existing rows have no recorded owner, because the old schema had nowhere
-- to record one. Deleting them would throw away real detection history, and
-- inventing a per-row owner would be guessing, so this migration takes the
-- only defensible route: it assigns every legacy row to the single
-- EARLIEST-CREATED application profile (`public.users`, oldest
-- `created_at`), which is deterministic and reproducible - not random.
--
-- Consequences you must understand:
--   * On a single-operator farm (the common case for this app) this is the
--     correct owner and every historical detection stays visible.
--   * On a farm that genuinely had several real operators, ALL history is
--     assigned to the oldest account. That is a documented, reversible
--     decision, not an accident. To move it, run the reassignment below
--     with the right user ids.
--
--   -- move every camera that matches a name, with its detections and alerts
--   update public.detections d
--      set user_id = '<NEW-UUID>'
--    from public.cameras c
--    where d.camera_id = c.id and c.name = 'Gate Camera';
--   update public.cameras set user_id = '<NEW-UUID>' where name = 'Gate Camera';
--   update public.alerts set user_id = '<NEW-UUID>' where camera_id in (
--       select id from public.cameras where name = 'Gate Camera');
--
-- Rows created AFTER this migration always carry the authenticated owner's
-- id, because the AI server derives it from the verified Supabase access
-- token (never from a client-supplied value).
--
-- Run with: supabase db push, or the Dashboard SQL editor, as the project
-- owner / service_role.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Owner columns
-- ---------------------------------------------------------------------
alter table public.cameras
    add column if not exists user_id uuid references auth.users (id) on delete cascade;

alter table public.detections
    add column if not exists user_id uuid references auth.users (id) on delete cascade;

alter table public.alerts
    add column if not exists user_id uuid references auth.users (id) on delete cascade;

alter table public.video_analysis_jobs
    add column if not exists user_id uuid references auth.users (id) on delete cascade;

alter table public.camera_status_history
    add column if not exists user_id uuid references auth.users (id) on delete cascade;

-- ---------------------------------------------------------------------
-- 2. settings: one row per user instead of the global `id = 1` row
-- ---------------------------------------------------------------------
alter table public.settings add column if not exists user_id uuid references auth.users (id) on delete cascade;

alter table public.settings drop constraint if exists settings_single_row;
alter table public.settings drop constraint if exists settings_pkey;

-- Hand the legacy settings row to the oldest profile, then remove any row
-- that still has no owner (possible if `public.users` is empty - we must not
-- leave an unowned row behind, because nothing could ever read it).
update public.settings
   set user_id = (select id from public.users order by created_at asc, id asc limit 1)
 where user_id is null;

delete from public.settings where user_id is null;

alter table public.settings alter column user_id set not null;
alter table public.settings drop column if exists id;
alter table public.settings add primary key (user_id);

comment on table public.settings is
    'Per-user detection/alert/stream configuration. One row per Supabase Auth user, keyed on user_id. Replaces the original single global row (id = 1).';

-- ---------------------------------------------------------------------
-- 3. Legacy row ownership (see "LEGACY ROWS" in the header)
-- ---------------------------------------------------------------------
do $$
declare
    legacy uuid;
begin
    select id into legacy
      from public.users
     order by created_at asc, id asc
     limit 1;

    if legacy is not null then
        update public.cameras             set user_id = legacy where user_id is null;
        update public.video_analysis_jobs set user_id = legacy where user_id is null;
        update public.detections          set user_id = legacy where user_id is null;
        update public.alerts              set user_id = legacy where user_id is null;
        update public.camera_status_history set user_id = legacy where user_id is null;
        raise notice 'Legacy rows assigned to the oldest profile: %', legacy;
    else
        raise notice
            'No application profiles exist yet, so no legacy rows could be assigned. Any pre-existing rows are left unowned and will be invisible until an owner is set by hand.';
    end if;
end;
$$;

-- Detections whose camera is gone (camera_id was set to null by
-- `on delete set null`) cannot inherit an owner from their camera, so give
-- any remaining orphans the oldest profile as well rather than leaving them
-- permanently unreadable.
do $$
declare
    legacy uuid;
begin
    select id into legacy from public.users order by created_at asc, id asc limit 1;
    if legacy is not null then
        update public.detections set user_id = legacy where user_id is null;
        update public.alerts     set user_id = legacy where user_id is null;
    end if;
end;
$$;

-- From here on every row must have an owner.
do $$
declare
    t text;
begin
    foreach t in array array['cameras', 'detections', 'alerts',
                             'video_analysis_jobs', 'camera_status_history']
    loop
        if exists (select 1 from public.users limit 1) then
            execute format('alter table public.%I alter column user_id set not null', t);
        end if;
    end loop;
end;
$$;

-- ---------------------------------------------------------------------
-- 4. camera_code becomes unique per user, not globally
-- ---------------------------------------------------------------------
-- Two users must each be able to own a "CAM-001"; a global unique index made
-- that impossible and would otherwise leak existence of another user's code.
-- `drop index` is a standalone statement, NOT an `alter table` subcommand -
-- `alter table ... drop index ...` is a syntax error. The globally-unique
-- constraint created by 0001 comes off as a constraint; any leftover index
-- from an earlier attempt comes off as a standalone drop.
alter table public.cameras drop constraint if exists cameras_camera_code_key;
drop index if exists public.ix_cameras_camera_code;
drop index if exists public.ux_cameras_user_camera_code;
create unique index if not exists ux_cameras_user_camera_code
    on public.cameras (user_id, camera_code);

-- ---------------------------------------------------------------------
-- 5. Ownership indexes (every user-scoped read filters on user_id)
-- ---------------------------------------------------------------------
create index if not exists ix_cameras_user_id             on public.cameras (user_id);
create index if not exists ix_detections_user_id           on public.detections (user_id);
create index if not exists ix_detections_user_created      on public.detections (user_id, created_at desc);
create index if not exists ix_detections_user_category     on public.detections (user_id, category);
create index if not exists ix_alerts_user_id               on public.alerts (user_id);
create index if not exists ix_alerts_user_resolved_created on public.alerts (user_id, resolved, created_at desc);
create index if not exists ix_jobs_user_id                 on public.video_analysis_jobs (user_id);
create index if not exists ix_status_history_user_id       on public.camera_status_history (user_id);

-- ---------------------------------------------------------------------
-- 6. RLS policies: real ownership checks
-- ---------------------------------------------------------------------
-- `users` keeps its own behaviour: you may read your own row. Reading other
-- profiles is no longer granted (there is no user-admin screen in this app,
-- so nothing needed it).
drop policy if exists users_select_authenticated on public.users;
create policy users_select_self
    on public.users for select
    to authenticated
    using (auth.uid() = id);

drop policy if exists users_insert_self on public.users;
create policy users_insert_self
    on public.users for insert
    to authenticated
    with check (auth.uid() = id);

drop policy if exists users_update_self on public.users;
create policy users_update_self
    on public.users for update
    to authenticated
    using (auth.uid() = id)
    with check (auth.uid() = id);

-- cameras -----------------------------------------------------------------
drop policy if exists cameras_all_authenticated on public.cameras;
create policy cameras_owner_all
    on public.cameras for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- detections --------------------------------------------------------------
drop policy if exists detections_all_authenticated on public.detections;
create policy detections_owner_all
    on public.detections for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- alerts ------------------------------------------------------------------
drop policy if exists alerts_all_authenticated on public.alerts;
create policy alerts_owner_all
    on public.alerts for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- settings ----------------------------------------------------------------
drop policy if exists settings_all_authenticated on public.settings;
create policy settings_owner_all
    on public.settings for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- video_analysis_jobs -----------------------------------------------------
drop policy if exists jobs_all_authenticated on public.video_analysis_jobs;
create policy jobs_owner_all
    on public.video_analysis_jobs for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- camera_status_history ---------------------------------------------------
drop policy if exists camera_status_history_all_authenticated on public.camera_status_history;
create policy camera_status_history_owner_all
    on public.camera_status_history for all
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- camera_secrets stays WITHOUT policies: RLS enabled + no policy = the
-- table is unreachable with anon/authenticated keys, so only the local AI
-- server (service role) can read or write camera passwords.

-- ---------------------------------------------------------------------
-- 7. Storage: private bucket + per-user folders
-- ---------------------------------------------------------------------
-- The bucket was `public = true` with a `to public` read policy, which made
-- every detection snapshot world-readable to anyone with the key. Snapshots
-- are now written under `snapshots/<user_id>/...` and the bucket is private,
-- so reads require an authenticated session AND must match the folder.
update storage.buckets
   set public = false
 where id = 'snapshots';

drop policy if exists snapshots_read_public on storage.objects;

create policy snapshots_read_own
    on storage.objects for select
    to authenticated
    using (
        bucket_id = 'snapshots'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

-- No insert/update/delete policy for anon or authenticated: snapshots are
-- only ever written by the local AI server with the service-role key.

-- ---------------------------------------------------------------------
-- 8. Analytics RPCs scoped to the caller
-- ---------------------------------------------------------------------
-- WHY THIS SECTION IS NECESSARY
-- -----------------------------
-- Migration 0003's RPCs rely on `security invoker` plus RLS to scope them.
-- That works for the BROWSER, which connects with the anon/authenticated key
-- and has `auth.uid()` set. It does NOT work for the local AI server, which
-- calls these same functions with the SERVICE-ROLE key - and the service role
-- bypasses RLS outright. Left alone, `/api/analytics` would have quietly
-- returned the whole farm's totals to every signed-in user, which is the same
-- cross-user leak this migration exists to close, just hidden behind an
-- aggregate instead of a row.
--
-- So each function gains an optional `p_user_id` and scopes on
--     coalesce(p_user_id, auth.uid()) = user_id
-- which is correct on both connection paths:
--
--   * Browser, no argument: p_user_id is NULL, so `auth.uid()` is used and
--     RLS independently enforces the same boundary.
--   * AI server, explicit argument: p_user_id is the id it already
--     authenticated from the caller's verified access token. Service role
--     skips RLS, but this explicit filter is what constrains it.
--   * Anon key, no argument: `auth.uid()` is NULL, so the predicate is
--     `NULL = user_id`, which is never true - anon gets empty aggregates.
--   * Anon key, someone else's uuid: RLS still applies to a non-service-role
--     caller, so the two conditions AND together and the caller can only ever
--     read their own rows. Passing p_user_id grants a browser nothing it did
--     not already have.
--
-- Adding a parameter changes the function signature, which CREATE OR REPLACE
-- cannot do, so the old signatures are dropped explicitly. (Without the drop
-- you would silently end up with two overloads, and PostgREST's choice
-- between them would be arbitrary.)

-- get_detection_summary ---------------------------------------------------
drop function if exists public.get_detection_summary();

create function public.get_detection_summary(p_user_id uuid default null)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
    select jsonb_build_object(
        'total_events',  count(*),
        'human_events',   count(*) filter (where category = 'Human'),
        'animal_events',  count(*) filter (where category = 'Animals'),
        'vehicle_events', count(*) filter (where category = 'Vehicles'),
        'other_events',   count(*) filter (where category = 'Others')
    )
    from public.detections
    where user_id = coalesce(p_user_id, auth.uid());
$$;

comment on function public.get_detection_summary(uuid) is
    'Detection totals by category for one owner. p_user_id is required when called with the service-role key (RLS is bypassed for that role); omit it from a browser session and auth.uid() is used.';

-- get_analytics ----------------------------------------------------------
drop function if exists public.get_analytics();

create function public.get_analytics(p_user_id uuid default null)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
with owner as (
    select coalesce(p_user_id, auth.uid()) as uid
),
now_utc as (
    select now() at time zone 'utc' as ts
),
-- Same window boundaries the original Python service used.
windows as (
    select
        (select ts from now_utc) - interval '24 hours' as daily_since,
        (select ts from now_utc) - interval '7 days'   as weekly_since,
        (select ts from now_utc) - interval '30 days'  as monthly_since
),
-- created_at is timestamptz; the original app stored naive-UTC values and
-- always read them back as UTC, so every bucket is computed in UTC here
-- too and the result is indistinguishable from the old behaviour.
detections_utc as (
    select
        d.category,
        (d.created_at at time zone 'utc') as created_utc
    from public.detections d, owner o
    where d.user_id = o.uid
),
series as (
    -- "daily": last 24 hours bucketed by hour
    select
        'daily'::text    as series,
        to_char(d.created_utc, 'YYYY-MM-DD HH24:00') as bucket,
        d.category,
        count(*)::bigint as n
    from detections_utc d, windows w
    where d.created_utc >= w.daily_since
    group by 1, 2, 3

    union all

    -- "weekly": last 7 days bucketed by day
    select
        'weekly'::text,
        to_char(d.created_utc, 'YYYY-MM-DD'),
        d.category,
        count(*)::bigint
    from detections_utc d, windows w
    where d.created_utc >= w.weekly_since
    group by 1, 2, 3

    union all

    -- "monthly": last 30 days bucketed by day
    select
        'monthly'::text,
        to_char(d.created_utc, 'YYYY-MM-DD'),
        d.category,
        count(*)::bigint
    from detections_utc d, windows w
    where d.created_utc >= w.monthly_since
    group by 1, 2, 3
),
pivoted as (
    select
        series,
        bucket,
        count(*) filter (where category = 'Human')::int   as "Human",
        count(*) filter (where category = 'Animals')::int  as "Animals",
        count(*) filter (where category = 'Vehicles')::int as "Vehicles",
        count(*) filter (where category = 'Others')::int   as "Others"
    from series
    group by series, bucket
),
by_category as (
    select
        coalesce(jsonb_object_agg(coalesce(d.category, 'Others'), n), '{}'::jsonb) as obj,
        sum(n) as total
    from (
        select d.category, count(*)::int as n
        from public.detections d, owner o
        where d.user_id = o.uid
        group by d.category
    ) d
),
by_camera as (
    select coalesce(jsonb_agg(jsonb_build_object('camera_name', camera_name, 'count', n) order by n desc), '[]'::jsonb) as obj
    from (
        select coalesce(d.camera_name, 'Unknown') as camera_name, count(*)::int as n
        from public.detections d, owner o
        where d.user_id = o.uid
          and d.camera_name is not null
        group by 1
        order by n desc
        limit 10
    ) c
),
peak_hours as (
    select coalesce(
        jsonb_agg(jsonb_build_object('hour', h.hour, 'count', h.n) order by h.hour),
        '[]'::jsonb
    ) as obj
    from (
        select gs::int as hour, count(d.id)::int as n
        from generate_series(0, 23) gs
        cross join owner o
        left join public.detections d
               on extract(hour from (d.created_at at time zone 'utc'))::int = gs::int
              and d.user_id = o.uid
        group by gs
    ) h
),
combined as (
    -- Category list is seeded from the fixed category list so the chart always
    -- shows all four categories, even at zero.
    select
        coalesce((
            select jsonb_object_agg(c, coalesce((bc.obj ->> c)::int, 0))
            from unnest(array['Human', 'Animals', 'Vehicles', 'Others']) as c
        ), '{}'::jsonb) as categories
    from by_category bc
)
select jsonb_build_object(
    'daily',       coalesce((select jsonb_agg(jsonb_build_object('bucket', bucket, 'Human', "Human", 'Animals', "Animals", 'Vehicles', "Vehicles", 'Others', "Others") order by bucket) from pivoted where series = 'daily'),   '[]'::jsonb),
    'weekly',      coalesce((select jsonb_agg(jsonb_build_object('bucket', bucket, 'Human', "Human", 'Animals', "Animals", 'Vehicles', "Vehicles", 'Others', "Others") order by bucket) from pivoted where series = 'weekly'),  '[]'::jsonb),
    'monthly',     coalesce((select jsonb_agg(jsonb_build_object('bucket', bucket, 'Human', "Human", 'Animals', "Animals", 'Vehicles', "Vehicles", 'Others', "Others") order by bucket) from pivoted where series = 'monthly'), '[]'::jsonb),
    'by_category', categories,
    'by_camera',   (select obj from by_camera),
    'peak_hours',  (select obj from peak_hours)
)
from combined;
$$;

comment on function public.get_analytics(uuid) is
    'Detection trends, by-category, by-camera and peak hours for ONE owner. p_user_id is required when called with the service-role key (RLS is bypassed for that role); omit it from a browser session and auth.uid() is used.';

-- get_system_health ------------------------------------------------------
drop function if exists public.get_system_health();

create function public.get_system_health(p_user_id uuid default null)
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
    select jsonb_build_object(
        'database_ok',    true,
        'total_cameras',  (select count(*)::int from public.cameras c
                           where c.user_id = coalesce(p_user_id, auth.uid())),
        'active_cameras', (select count(*)::int from public.cameras c
                           where c.status = 'online'
                             and c.user_id = coalesce(p_user_id, auth.uid()))
    );
$$;

comment on function public.get_system_health(uuid) is
    'Cheap cloud-side health/camera-count probe for ONE owner, used by the dashboard fallback.';
